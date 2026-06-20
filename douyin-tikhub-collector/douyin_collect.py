#!/usr/bin/env python3
"""Collect Douyin video data through TikHub."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import subprocess
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.tikhub.io"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "采集文件夹"
SHORT_LINK_USER_AGENTS = [
    "douyin-tikhub-collector/1.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
]
API_PATHS = {
    "share": "/api/v1/douyin/app/v3/fetch_one_video_by_share_url",
    "aweme": "/api/v1/douyin/app/v3/fetch_one_video_v3",
    "quality": "/api/v1/douyin/app/v3/fetch_video_highest_quality_play_url",
}
SUCCESS_CODES = {0, 200}
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".mov",
    ".m4v",
    ".m4a",
    ".mp3",
    ".wav",
    ".ogg",
    ".aac",
    ".webm",
    ".mkv",
    ".flv",
    ".m3u8",
    ".vvic",
}
MEDIA_HOST_HINTS = (
    "douyin",
    "byteimg",
    "douyinvod",
    "douyinpic",
    "snssdk",
    "amemv",
    "aweme",
    "bytedance",
    "pstatp",
)
AUDIO_SOURCE_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".m4a",
    ".mp3",
    ".wav",
    ".ogg",
    ".aac",
    ".webm",
    ".mkv",
    ".flv",
}


class DownloadRateLimiter:
    def __init__(self, qps: float) -> None:
        self.min_interval = 1.0 / qps if qps and qps > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                wait_time = self._next_allowed - now
                if wait_time <= 0:
                    self._next_allowed = now + self.min_interval
                    return
            time.sleep(min(wait_time, self.min_interval))


class CollectError(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(
    url: str,
    token: str,
    params: dict[str, str],
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(params)
    full_url = f"{url}?{encoded}" if encoded else url
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "douyin-tikhub-collector/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read().decode(charset, errors="replace")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = CollectError(f"HTTP {exc.code}: {body[:800]}")
            if exc.code in {400, 401, 402, 403, 404}:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc

        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))

    raise CollectError(str(last_error) if last_error else "request failed")


def looks_successful(data: dict[str, Any]) -> bool:
    code = data.get("code")
    if code in SUCCESS_CODES or str(code).lower() in {"0", "200", "success"}:
        return True
    message = str(data.get("message") or data.get("msg") or "").lower()
    has_payload = any(data.get(key) for key in ("data", "result", "aweme_detail", "aweme"))
    return has_payload and not any(word in message for word in ("error", "fail", "invalid"))


def iter_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_values(item)
    else:
        yield value


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def find_first_by_keys(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in keys and item not in (None, "", [], {}):
                return item
        for item in value.values():
            found = find_first_by_keys(item, keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_first_by_keys(item, keys)
            if found not in (None, "", [], {}):
                return found
    return None


def extract_aweme_id(source: str) -> str | None:
    patterns = [
        r"(?:modal_id)=([0-9]{10,30})",
        r"(?:aweme_id|awemeId|item_id|itemId)=([0-9]{10,30})",
        r"/video/([0-9]{10,30})",
        r"/note/([0-9]{10,30})",
        r"\b([0-9]{18,25})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return None


def extract_first_url(source: str) -> str | None:
    match = re.search(r"https?://[^\s\"'<>\)\]]+", source)
    if not match:
        return None
    return match.group(0).rstrip(".,;，。")


def resolve_douyin_short_link(source: str, timeout: int) -> str | None:
    raw_url = extract_first_url(source)
    if not raw_url:
        return None
    parsed = urllib.parse.urlparse(raw_url)
    host = parsed.netloc.lower()
    if "v.douyin.com" not in host:
        return raw_url

    for user_agent in SHORT_LINK_USER_AGENTS:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        req = urllib.request.Request(raw_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=min(timeout, 15)) as response:
                final_url = response.geturl()
        except Exception:
            continue
        final_host = urllib.parse.urlparse(final_url).netloc.lower()
        if "douyin.com" in final_host or "iesdouyin.com" in final_host:
            return final_url
    return None


def content_type_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.lower()
    if "/note/" in path:
        return "image"
    if "/video/" in path:
        return "video"
    return None


def safe_name(value: str, fallback: str = "douyin-item", max_length: int = 80) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    value = value[:max_length].strip(" .-_")
    return value or fallback


def safe_path_name(value: str, fallback: str = "douyin-item", max_bytes: int = 180) -> str:
    value = safe_name(value, fallback=fallback, max_length=240)
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1].strip(" .-_")
    return value or fallback


def first_url(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        if isinstance(value, list):
            found = first_url(*value)
            if found:
                return found
    return None


def usable_image_url(value: Any) -> str | None:
    urls: list[str] = []
    if isinstance(value, str):
        urls = [value]
    elif isinstance(value, list):
        urls = [item for item in value if isinstance(item, str)]
    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in (".jpeg", ".jpg", ".webp", ".png")) and "_vvic" not in lowered:
            return url
    return first_url(urls)


def find_aweme_item(data: dict[str, Any], source: str) -> dict[str, Any] | None:
    aweme_id = extract_aweme_id(source)
    for item in iter_dicts(data):
        item_id = str(item.get("aweme_id") or item.get("id") or item.get("item_id") or "")
        if aweme_id and item_id == aweme_id and (
            item.get("video") or item.get("statistics") or item.get("author")
        ):
            return item
    for item in iter_dicts(data):
        if item.get("video") and (item.get("statistics") or item.get("author")):
            return item
    return None


def build_slug(data: dict[str, Any], source: str) -> str:
    item = find_aweme_item(data, source) or {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    nickname = (
        author.get("nickname")
        or author.get("unique_id")
        or find_first_by_keys(data, {"nickname", "unique_id", "sec_uid"})
    )
    title = (
        item.get("desc")
        or item.get("title")
        or find_first_by_keys(data, {"desc", "title", "share_title"})
    )
    if nickname or title:
        return safe_path_name(f"{nickname or 'unknown'}-{title or 'untitled'}")
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def summarize(data: dict[str, Any], endpoint: str, source: str) -> dict[str, Any]:
    item = find_aweme_item(data, source) or {}
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    images = item.get("images") if isinstance(item.get("images"), list) else []
    note_type = "image" if images else "video"
    summary = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "endpoint": endpoint,
        "note_type": note_type,
    }

    fields = {
        "aweme_id": item.get("aweme_id") or extract_aweme_id(source),
        "title": item.get("desc") or item.get("title"),
        "nickname": author.get("nickname") or author.get("unique_id"),
        "user_id": author.get("uid") or author.get("short_id") or author.get("sec_uid"),
        "liked_count": statistics.get("digg_count"),
        "collected_count": statistics.get("collect_count"),
        "comment_count": statistics.get("comment_count"),
        "share_count": statistics.get("share_count"),
        "download_count": statistics.get("download_count"),
    }
    for key, value in fields.items():
        if value not in (None, "", [], {}):
            summary[key] = value

    if "title" not in summary:
        found_title = find_first_by_keys(data, {"desc", "title", "share_title"})
        if found_title:
            summary["title"] = found_title
    if "nickname" not in summary:
        found_nickname = find_first_by_keys(data, {"nickname", "unique_id"})
        if found_nickname:
            summary["nickname"] = found_nickname
    return summary


def is_media_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    suffix = Path(parsed.path.lower()).suffix
    if suffix in MEDIA_EXTENSIONS:
        return True
    host = parsed.netloc.lower()
    return any(hint in host for hint in MEDIA_HOST_HINTS)


def collect_media_urls(data: Any) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for value in iter_values(data):
        if not isinstance(value, str):
            continue
        candidates = [value] if value.startswith(("http://", "https://")) else []
        candidates.extend(
            match.group(0) for match in re.finditer(r"https?://[^\s\"'<>\)\]]+", value)
        )
        for candidate in candidates:
            url = candidate.rstrip(".,;")
            if url not in seen and is_media_url(url):
                seen.add(url)
                urls.append(url)
    return urls


def primary_media(data: dict[str, Any], source: str) -> dict[str, str]:
    item = find_aweme_item(data, source) or {}
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    images = item.get("images") if isinstance(item.get("images"), list) else []
    cover = video.get("cover") if isinstance(video.get("cover"), dict) else {}
    origin_cover = video.get("origin_cover") if isinstance(video.get("origin_cover"), dict) else {}
    dynamic_cover = video.get("dynamic_cover") if isinstance(video.get("dynamic_cover"), dict) else {}
    play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
    download_addr = video.get("download_addr") if isinstance(video.get("download_addr"), dict) else {}
    video_url = first_url(
        play_addr.get("url_list"),
        download_addr.get("url_list"),
        video.get("play_url"),
        video.get("download_url"),
    )

    result: dict[str, str] = {}
    if images:
        seen_urls: set[str] = set()
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            image_url = usable_image_url(image.get("url_list")) or usable_image_url(
                image.get("download_url_list")
            )
            if not image_url or image_url in seen_urls:
                continue
            key = "cover" if index == 1 else f"image_{index:02d}"
            result[key] = image_url
            seen_urls.add(image_url)
        if video_url:
            result["audio"] = video_url
        return result

    cover_url = first_url(
        origin_cover.get("url_list"),
        cover.get("url_list"),
        dynamic_cover.get("url_list"),
        video.get("cover_url"),
    )
    dynamic_cover_url = first_url(dynamic_cover.get("url_list"))
    if cover_url:
        result["cover"] = cover_url
    if dynamic_cover_url and dynamic_cover_url != cover_url:
        result["dynamic_cover"] = dynamic_cover_url
    if video_url:
        result["video"] = video_url
    return result


def guess_extension(url: str, content_type: str | None) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if suffix.lower() in MEDIA_EXTENSIONS:
        return suffix.lower()
    if content_type:
        normalized_type = content_type.split(";")[0].strip().lower()
        if normalized_type == "image/vvic":
            return ".vvic"
        guessed = mimetypes.guess_extension(normalized_type)
        if guessed:
            return guessed
    return ".bin"


def can_extract_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SOURCE_EXTENSIONS


def audio_target_path(media_path: Path, base_name: str, media_name: str | None = None) -> Path:
    if media_name in {"video", "audio"}:
        return media_path.with_name(f"{base_name}-audio.mp3")
    return media_path.with_name(f"{media_path.stem}-audio.mp3")


def extract_audio_file(
    media_path: Path,
    base_name: str,
    media_name: str | None,
    sample_rate: int,
    channels: int,
    bitrate: str,
) -> Path:
    if not can_extract_audio(media_path):
        raise CollectError(f"unsupported audio source file type: {media_path}")

    target = audio_target_path(media_path, base_name, media_name)
    same_path = media_path.resolve() == target.resolve()
    output_path = target.with_name(f"{target.stem}.tmp{target.suffix}") if same_path else target

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise CollectError("ffmpeg not found; cannot extract mp3 audio")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_path),
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CollectError(detail or f"ffmpeg exited with code {result.returncode}")
    if same_path:
        output_path.replace(target)
    return target


def download_named_url(
    url: str,
    target_dir: Path,
    base_name: str,
    name: str,
    timeout: int,
    retries: int = 0,
    limiter: DownloadRateLimiter | None = None,
) -> Path:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return download_named_url_once(url, target_dir, base_name, name, timeout, limiter)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_error or CollectError("download failed")


def download_named_url_once(
    url: str,
    target_dir: Path,
    base_name: str,
    name: str,
    timeout: int,
    limiter: DownloadRateLimiter | None = None,
) -> Path:
    if limiter:
        limiter.wait()
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        ext = guess_extension(url, response.headers.get("Content-Type"))
        if name in {"video", "audio"} and ext == ".bin":
            ext = ".mp4"
        target = target_dir / f"{base_name}-{name}{ext}"
        temp_target = target.with_name(f"{target.name}.part")
        try:
            with temp_target.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
            temp_target.replace(target)
        except Exception:
            if temp_target.exists():
                temp_target.unlink()
            raise
    return target


def download_named_media(task: tuple[str, str, Path, str, int, int, DownloadRateLimiter | None]) -> dict[str, Any]:
    name, url, target_dir, base_name, timeout, retries, limiter = task
    try:
        media_path = download_named_url(url, target_dir, base_name, name, timeout, retries, limiter)
        return {"ok": True, "name": name, "url": url, "path": str(media_path)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "name": name, "url": url, "error": str(exc)}


def local_media_keys(primary: dict[str, str], media_dir: Path) -> set[str]:
    keys: set[str] = set()
    if not media_dir.exists():
        return keys
    files = [path for path in media_dir.iterdir() if path.is_file() and path.stat().st_size > 0]
    for key in primary:
        token = f"-{key}."
        if any(token in path.name for path in files):
            keys.add(key)
    return keys


def media_completion(primary: dict[str, str], media_dir: Path, media_errors: list[dict[str, str]]) -> dict[str, Any]:
    expected = list(primary.keys())
    present = sorted(local_media_keys(primary, media_dir))
    missing = [key for key in expected if key not in present]
    missing_set = set(missing)
    blocking_errors = [error for error in media_errors if error.get("name") in missing_set]
    return {
        "complete": not missing,
        "expected": expected,
        "present": present,
        "missing": missing,
        "blocking_errors": blocking_errors,
    }


def call_video_api(
    source: str,
    token: str,
    base_url: str,
    timeout: int,
    retries: int,
    use_quality: bool,
) -> tuple[str, dict[str, Any]]:
    resolved_source = resolve_douyin_short_link(source, timeout) or source
    aweme_id = extract_aweme_id(resolved_source) or extract_aweme_id(source)
    first_url = extract_first_url(resolved_source)
    if aweme_id:
        order = [("aweme", {"aweme_id": aweme_id})]
    else:
        order = [("share", {"share_url": first_url or source})]
    if use_quality:
        if not aweme_id:
            raise CollectError("--highest-quality requires an aweme_id in the input or URL")
        order.insert(0, ("quality", {"aweme_id": aweme_id}))

    errors: list[str] = []
    for endpoint_name, params in order:
        url = base_url.rstrip("/") + API_PATHS[endpoint_name]
        try:
            data = request_json(url, token, params, timeout=timeout, retries=retries)
        except CollectError as exc:
            errors.append(f"{endpoint_name}: {exc}")
            continue
        if looks_successful(data):
            return endpoint_name, data
        message = data.get("message") or data.get("msg") or data.get("code")
        errors.append(f"{endpoint_name}: API returned non-success response: {message}")

    raise CollectError("; ".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Douyin video JSON through TikHub.")
    parser.add_argument("source", help="Douyin share text, URL, or aweme_id")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--env-file", default=".env", help="Optional env file containing TIKHUB_API_KEY.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TIKHUB_BASE_URL", DEFAULT_BASE_URL),
        help="TikHub API base URL. Use https://api.tikhub.dev in mainland China if needed.",
    )
    parser.add_argument("--download-media", action="store_true", help="Download core cover/video files.")
    parser.add_argument(
        "--no-extract-audio",
        action="store_true",
        help="Do not create a standard MP3 after downloading video/audio media.",
    )
    parser.add_argument("--audio-sample-rate", type=int, default=16000)
    parser.add_argument("--audio-channels", type=int, default=1, choices=[1, 2])
    parser.add_argument("--audio-bitrate", default="64k")
    parser.add_argument(
        "--highest-quality",
        action="store_true",
        help="Use TikHub highest-quality play-url endpoint first. This may incur extra billing.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--media-workers", type=int, default=4, help="Parallel media download workers.")
    parser.add_argument("--media-qps", type=float, default=10.0, help="Maximum media download requests per second.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(Path(args.env_file))
    sibling_env = Path(__file__).resolve().parents[1] / "xhs-tikhub-collector" / ".env"
    load_dotenv(sibling_env)

    token = os.environ.get("TIKHUB_API_KEY") or os.environ.get("TIKHUB_TOKEN")
    if not token:
        print("Missing API key. Set TIKHUB_API_KEY in .env or the environment.", file=sys.stderr)
        return 2

    try:
        endpoint, data = call_video_api(
            source=args.source,
            token=token,
            base_url=args.base_url,
            timeout=args.timeout,
            retries=args.retries,
            use_quality=args.highest_quality,
        )
    except CollectError as exc:
        print(f"Collect failed: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser().resolve()
    slug = build_slug(data, args.source)
    item_dir = out_dir / slug
    item_dir.mkdir(parents=True, exist_ok=True)

    raw_path = item_dir / f"{slug}-raw.json"
    summary_path = item_dir / f"{slug}-summary.json"
    primary_media_path = item_dir / f"{slug}-primary_media.json"
    media_urls_path = item_dir / f"{slug}-media_urls.txt"

    summary_data = summarize(data, endpoint, args.source)
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")
    primary = primary_media(data, args.source)
    primary_media_path.write_text(json.dumps(primary, ensure_ascii=False, indent=2), encoding="utf-8")
    media_urls = collect_media_urls(data)
    media_urls_path.write_text("\n".join(media_urls), encoding="utf-8")

    downloaded: list[dict[str, str]] = []
    extracted_audio: list[dict[str, str]] = []
    media_errors: list[dict[str, str]] = []
    audio_errors: list[dict[str, str]] = []
    if args.download_media and primary:
        media_dir = item_dir / "media"
        media_dir.mkdir(exist_ok=True)
        limiter = DownloadRateLimiter(args.media_qps)
        tasks = [
            (name, url, media_dir, slug, args.timeout, args.retries, limiter)
            for name, url in primary.items()
        ]
        with ThreadPoolExecutor(max_workers=max(1, args.media_workers)) as pool:
            results = list(pool.map(download_named_media, tasks))
        for result in results:
            name = result["name"]
            url = result["url"]
            if result["ok"]:
                media_path = Path(result["path"])
                downloaded.append({"name": name, "url": url, "path": str(media_path)})
            else:
                print(f"Media download failed: {url} ({result['error']})", file=sys.stderr)
                media_errors.append({"name": name, "url": url, "error": result["error"]})
                continue
            if not args.no_extract_audio and can_extract_audio(media_path):
                try:
                    audio_path = extract_audio_file(
                        media_path=media_path,
                        base_name=slug,
                        media_name=name,
                        sample_rate=args.audio_sample_rate,
                        channels=args.audio_channels,
                        bitrate=args.audio_bitrate,
                    )
                    extracted_audio.append(
                        {
                            "source_name": name,
                            "source_path": str(media_path),
                            "path": str(audio_path),
                            "format": "mp3",
                            "sample_rate": str(args.audio_sample_rate),
                            "channels": str(args.audio_channels),
                            "bitrate": args.audio_bitrate,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"Audio extraction failed: {media_path} ({exc})", file=sys.stderr)
                    audio_errors.append({"name": name, "path": str(media_path), "error": str(exc)})

    local_media_path: Path | None = None
    completion = media_completion(primary, item_dir / "media", media_errors) if args.download_media else {
        "complete": not bool(primary),
        "expected": list(primary.keys()),
        "present": [],
        "missing": [],
        "blocking_errors": [],
    }
    if args.download_media:
        local_media_path = item_dir / f"{slug}-local_media.json"
        local_media_path.write_text(
            json.dumps(
                {
                    "downloaded": downloaded,
                    "extracted_audio": extracted_audio,
                    "media_errors": media_errors,
                    "audio_errors": audio_errors,
                    "media_complete": completion["complete"],
                    "expected_media": completion["expected"],
                    "present_media": completion["present"],
                    "missing_media": completion["missing"],
                    "blocking_errors": completion["blocking_errors"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        summary_data["local_media"] = str(local_media_path)
        summary_data["media_complete"] = completion["complete"]
        summary_data["missing_media"] = completion["missing"]
    if extracted_audio:
        summary_data["local_audio"] = extracted_audio[0]["path"]
    if args.download_media or extracted_audio:
        summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "ok": True,
        "endpoint": endpoint,
        "directory": str(item_dir),
        "raw_json": str(raw_path),
        "summary_json": str(summary_path),
        "primary_media": str(primary_media_path),
        "media_urls": len(media_urls),
        "downloaded_media": len(downloaded),
        "extracted_audio": len(extracted_audio),
        "media_complete": completion["complete"],
        "missing_media": completion["missing"],
    }
    if local_media_path:
        result["local_media"] = str(local_media_path)
    if extracted_audio:
        result["local_audio"] = extracted_audio[0]["path"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
