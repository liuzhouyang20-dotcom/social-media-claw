#!/usr/bin/env python3
"""Collect Xiaohongshu note data through TikHub."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.tikhub.io"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "采集文件夹"
API_PATHS = {
    "image": "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
    "video": "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
    "app_v1": "/api/v1/xiaohongshu/app/get_note_info",
}
SUCCESS_CODES = {0, 200}
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".heic",
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
MEDIA_HOST_HINTS = (
    "xhscdn",
    "xhs",
    "sns-img",
    "sns-video",
    "xiaohongshu",
    "redcdn",
    "rednotecdn",
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
        "User-Agent": "xhs-tikhub-collector/1.0",
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
    if code in SUCCESS_CODES:
        return True
    if str(code).lower() in {"0", "200", "success"}:
        return True

    message = str(data.get("message") or data.get("msg") or "").lower()
    has_payload = any(data.get(key) for key in ("data", "result", "aweme_detail", "note"))
    if has_payload and not any(word in message for word in ("error", "fail", "invalid")):
        return True
    return False


def text_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(text_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(text_values(item))
    elif isinstance(value, str):
        values.append(value)
    return values


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


def extract_note_id(source: str) -> str | None:
    patterns = [
        r"/(?:discovery/)?item/([0-9a-fA-F]{16,32})",
        r"/explore/([0-9a-fA-F]{16,32})",
        r"(?:note_id|noteId|item_id|itemId)=([0-9a-fA-F]{16,32})",
        r"\b([0-9a-fA-F]{24})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return None


def safe_name(value: str, fallback: str = "xhs-note", max_length: int = 100) -> str:
    value = value.strip()[:max_length]
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    return value or fallback


def build_slug(data: dict[str, Any], source: str) -> str:
    item = find_note_item(data, source) or {}
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    author = (
        user.get("nickname")
        or user.get("name")
        or find_first_by_keys(data, {"nickname", "nick_name", "username", "user_name"})
    )
    title = (
        item.get("title")
        or find_first_by_keys(data, {"title", "display_title"})
        or item.get("desc")
        or find_first_by_keys(data, {"desc", "description", "content"})
    )
    if author or title:
        return safe_name(f"{author or 'unknown'}-{title or 'untitled'}", max_length=120)
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def summarize(data: dict[str, Any], endpoint: str, source: str) -> dict[str, Any]:
    keys = {
        "note_id": {"note_id", "noteid", "id", "noteidstr"},
        "title": {"title", "display_title"},
        "description": {"desc", "description", "content"},
        "nickname": {"nickname", "nick_name", "username", "user_name"},
        "user_id": {"user_id", "userid", "user_id_str"},
        "liked_count": {"liked_count", "like_count", "likes"},
        "collected_count": {"collected_count", "collect_count", "collects"},
        "comment_count": {"comment_count", "comments_count", "comments"},
        "share_count": {"share_count", "shares"},
    }
    summary = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "endpoint": endpoint,
    }
    for output_key, possible_keys in keys.items():
        found = find_first_by_keys(data, possible_keys)
        if found not in (None, "", [], {}):
            summary[output_key] = found
    return summary


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def find_note_item(data: dict[str, Any], source: str) -> dict[str, Any] | None:
    note_id = extract_note_id(source)
    for item in iter_dicts(data):
        if (
            note_id
            and str(item.get("id") or item.get("note_id") or "") == note_id
            and (item.get("video_info_v2") or item.get("images_list"))
        ):
            return item
    for item in iter_dicts(data):
        if item.get("video_info_v2") or item.get("images_list"):
            return item
    return None


def first_url(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def best_stream_url(streams: dict[str, Any]) -> str | None:
    candidates: list[dict[str, Any]] = []
    for codec in ("h264", "h265", "av1", "h266"):
        codec_streams = streams.get(codec)
        if isinstance(codec_streams, list):
            candidates.extend(item for item in codec_streams if isinstance(item, dict))
    if not candidates:
        return None

    def score(item: dict[str, Any]) -> tuple[int, int]:
        codec_score = 2 if item.get("video_codec") == "h264" else 1
        return codec_score, int(item.get("video_bitrate") or 0)

    best = max(candidates, key=score)
    return first_url(best.get("master_url"), *(best.get("backup_urls") or []))


def primary_media(data: dict[str, Any], source: str) -> dict[str, str]:
    item = find_note_item(data, source) or {}
    video_info = item.get("video_info_v2") if isinstance(item, dict) else {}
    if not isinstance(video_info, dict):
        video_info = {}

    image_info = video_info.get("image") if isinstance(video_info.get("image"), dict) else {}
    media = video_info.get("media") if isinstance(video_info.get("media"), dict) else {}
    streams = media.get("stream") if isinstance(media.get("stream"), dict) else {}

    result: dict[str, str] = {}
    images = item.get("images_list") if isinstance(item, dict) else None
    first_image = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else {}
    post_cover = first_url(
        first_image.get("original"),
        first_image.get("url"),
        item.get("share_info", {}).get("image") if isinstance(item.get("share_info"), dict) else None,
    )
    video_thumbnail = first_url(
        image_info.get("thumbnail"),
        image_info.get("thumbnail_dim"),
        image_info.get("first_frame"),
    )
    first_frame = first_url(image_info.get("first_frame"), video_thumbnail)
    video = best_stream_url(streams)

    seen_urls: set[str] = set()
    if post_cover:
        result["cover"] = post_cover
        seen_urls.add(post_cover)
    if isinstance(images, list):
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            image_url = first_url(image.get("original"), image.get("url"))
            if not image_url or image_url in seen_urls:
                continue
            result[f"image_{index:02d}"] = image_url
            seen_urls.add(image_url)
    if video_thumbnail:
        result["video_thumbnail"] = video_thumbnail
    if first_frame:
        result["first_frame"] = first_frame
    if video:
        result["video"] = video
    return {key: value for key, value in result.items() if value}


def is_media_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    suffix = Path(path).suffix
    if suffix in MEDIA_EXTENSIONS:
        return True
    host = parsed.netloc.lower()
    return any(hint in host for hint in MEDIA_HOST_HINTS)


def infer_note_type(source: str) -> str | None:
    parsed = urllib.parse.urlparse(source)
    query_type = urllib.parse.parse_qs(parsed.query).get("type", [""])[0].lower()
    if query_type in {"video", "image"}:
        return query_type
    if "type=video" in source.lower():
        return "video"
    return None


def collect_media_urls(data: Any) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for text in text_values(data):
        candidates = [text] if text.startswith(("http://", "https://")) else []
        candidates.extend(
            match.group(0) for match in re.finditer(r"https?://[^\s\"'<>\)\]]+", text)
        )
        for candidate in candidates:
            url = candidate.rstrip(".,;")
            if url not in seen and is_media_url(url):
                seen.add(url)
                urls.append(url)
    return urls


def guess_extension(url: str, content_type: str | None) -> str:
    path_suffix = Path(urllib.parse.urlparse(url).path).suffix
    if path_suffix.lower() in MEDIA_EXTENSIONS:
        return path_suffix.lower()
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
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


def download_url(url: str, target_dir: Path, index: int, timeout: int, base_name: str = "") -> Path:
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type")
        ext = guess_extension(url, content_type)
        prefix = f"{base_name}-" if base_name else ""
        target = target_dir / f"{prefix}{index:03d}{ext}"
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
    return target


def download_named_url(url: str, target_dir: Path, base_name: str, name: str, timeout: int) -> Path:
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type")
        ext = guess_extension(url, content_type)
        if name in {"video", "audio"} and ext == ".bin":
            ext = ".mp4"
        target = target_dir / f"{base_name}-{name}{ext}"
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
    return target


def call_note_api(
    source: str,
    note_type: str,
    token: str,
    base_url: str,
    timeout: int,
    retries: int,
    fallback: bool,
) -> tuple[str, dict[str, Any]]:
    note_id = extract_note_id(source)
    params = {"note_id": note_id} if note_id else {"share_text": source}

    inferred_type = infer_note_type(source)
    if note_type == "auto" and inferred_type == "video":
        order = ["video", "image"]
    elif note_type == "auto":
        order = ["image", "video"]
    else:
        order = [note_type]
    if fallback and "app_v1" not in order:
        order.append("app_v1")

    errors: list[str] = []
    for endpoint_name in order:
        path = API_PATHS[endpoint_name]
        url = base_url.rstrip("/") + path
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
    parser = argparse.ArgumentParser(
        description="Collect Xiaohongshu note JSON through TikHub.",
    )
    parser.add_argument("source", help="Xiaohongshu share text, URL, or note_id")
    parser.add_argument(
        "--type",
        choices=["auto", "image", "video"],
        default="auto",
        help="Note type. auto tries image first, then video.",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional env file containing TIKHUB_API_KEY.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TIKHUB_BASE_URL", DEFAULT_BASE_URL),
        help="TikHub API base URL. Use https://api.tikhub.dev in mainland China if needed.",
    )
    parser.add_argument(
        "--download-media",
        action="store_true",
        help="Also download media URLs found in the JSON response.",
    )
    parser.add_argument(
        "--no-extract-audio",
        action="store_true",
        help="Do not create a standard MP3 after downloading video/audio media.",
    )
    parser.add_argument("--audio-sample-rate", type=int, default=16000)
    parser.add_argument("--audio-channels", type=int, default=1, choices=[1, 2])
    parser.add_argument("--audio-bitrate", default="64k")
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Do not try the App V1 fallback endpoint.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(Path(args.env_file))

    token = os.environ.get("TIKHUB_API_KEY") or os.environ.get("TIKHUB_TOKEN")
    if not token:
        print(
            "Missing API key. Set TIKHUB_API_KEY in the environment or in .env.",
            file=sys.stderr,
        )
        return 2

    try:
        endpoint, data = call_note_api(
            source=args.source,
            note_type=args.type,
            token=token,
            base_url=args.base_url,
            timeout=args.timeout,
            retries=args.retries,
            fallback=not args.no_fallback,
        )
    except CollectError as exc:
        print(f"Collect failed: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out).expanduser().resolve()
    slug = build_slug(data, args.source)
    note_dir = out_dir / slug
    note_dir.mkdir(parents=True, exist_ok=True)

    raw_path = note_dir / f"{slug}-raw.json"
    summary_path = note_dir / f"{slug}-summary.json"
    summary_data = summarize(data, endpoint, args.source)
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    downloaded: list[dict[str, str]] = []
    extracted_audio: list[dict[str, str]] = []
    media_errors: list[dict[str, str]] = []
    audio_errors: list[dict[str, str]] = []
    primary = primary_media(data, args.source)
    primary_media_path = note_dir / f"{slug}-primary_media.json"
    primary_media_path.write_text(
        json.dumps(primary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    media_urls = collect_media_urls(data)
    media_urls_path = note_dir / f"{slug}-media_urls.txt"
    media_urls_path.write_text("\n".join(media_urls), encoding="utf-8")

    if args.download_media and primary:
        media_dir = note_dir / "media"
        media_dir.mkdir(exist_ok=True)
        for name, url in primary.items():
            try:
                media_path = download_named_url(url, media_dir, slug, name, args.timeout)
                downloaded.append({"name": name, "url": url, "path": str(media_path)})
            except Exception as exc:  # noqa: BLE001 - keep the rest of the downloads moving
                print(f"Media download failed: {url} ({exc})", file=sys.stderr)
                media_errors.append({"name": name, "url": url, "error": str(exc)})
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
    elif args.download_media and media_urls:
        media_dir = note_dir / "media"
        media_dir.mkdir(exist_ok=True)
        for index, url in enumerate(media_urls, start=1):
            try:
                media_path = download_url(url, media_dir, index, args.timeout, slug)
                downloaded.append({"name": f"{index:03d}", "url": url, "path": str(media_path)})
            except Exception as exc:  # noqa: BLE001 - keep the rest of the downloads moving
                print(f"Media download failed: {url} ({exc})", file=sys.stderr)
                media_errors.append({"name": f"{index:03d}", "url": url, "error": str(exc)})
                continue
            if not args.no_extract_audio and can_extract_audio(media_path):
                try:
                    audio_path = extract_audio_file(
                        media_path=media_path,
                        base_name=slug,
                        media_name=None,
                        sample_rate=args.audio_sample_rate,
                        channels=args.audio_channels,
                        bitrate=args.audio_bitrate,
                    )
                    extracted_audio.append(
                        {
                            "source_name": f"{index:03d}",
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
                    audio_errors.append({"name": f"{index:03d}", "path": str(media_path), "error": str(exc)})

    local_media_path: Path | None = None
    if args.download_media:
        local_media_path = note_dir / f"{slug}-local_media.json"
        local_media_path.write_text(
            json.dumps(
                {
                    "downloaded": downloaded,
                    "extracted_audio": extracted_audio,
                    "media_errors": media_errors,
                    "audio_errors": audio_errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        summary_data["local_media"] = str(local_media_path)
    if extracted_audio:
        summary_data["local_audio"] = extracted_audio[0]["path"]
    if args.download_media or extracted_audio:
        summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = {
        "ok": True,
        "endpoint": endpoint,
        "directory": str(note_dir),
        "raw_json": str(raw_path),
        "summary_json": str(summary_path),
        "primary_media": str(primary_media_path),
        "media_urls": len(media_urls),
        "downloaded_media": len(downloaded),
        "extracted_audio": len(extracted_audio),
    }
    if local_media_path:
        result["local_media"] = str(local_media_path)
    if extracted_audio:
        result["local_audio"] = extracted_audio[0]["path"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
