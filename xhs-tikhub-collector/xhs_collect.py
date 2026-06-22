#!/usr/bin/env python3
"""Collect Xiaohongshu note data through TikHub."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
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
    "xhs-tikhub-collector/1.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
]
API_PATHS = {
    "image": "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
    "video": "/api/v1/xiaohongshu/app_v2/get_video_note_detail",
    "web_v3": "/api/v1/xiaohongshu/web_v3/fetch_note_detail",
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


def has_collectable_note_payload(data: dict[str, Any], source: str) -> bool:
    if find_note_item(data, source):
        return True
    if primary_media(data, source):
        return True
    payload = data.get("data")
    if payload in (None, "", [], {}):
        return False
    summary = summarize(data, "validation", source)
    return any(summary.get(key) for key in ("title", "description", "nickname", "note_id"))


def incomplete_note_payload_reason(data: dict[str, Any], source: str) -> str | None:
    item = find_note_item(data, source) or {}
    note_type = str(item.get("type") or "").lower()
    if note_type == "video" and not primary_media(data, source).get("video"):
        return "API returned a video note without a video URL"
    return None


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


def extract_xsec_token(source: str) -> str | None:
    raw_url = extract_first_url(source) or source
    parsed = urllib.parse.urlparse(raw_url)
    token = urllib.parse.parse_qs(parsed.query).get("xsec_token", [""])[0]
    return token or None


def extract_first_url(source: str) -> str | None:
    match = re.search(r"https?://[^\s\"'<>\)\]]+", source)
    if not match:
        return None
    return match.group(0).rstrip(".,;，。")


def resolve_xhs_short_link(source: str, timeout: int) -> str | None:
    raw_url = extract_first_url(source)
    if not raw_url:
        return None
    parsed = urllib.parse.urlparse(raw_url)
    if "xhslink.com" not in parsed.netloc.lower():
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
        if "xiaohongshu.com" in urllib.parse.urlparse(final_url).netloc.lower():
            return final_url
    return None


def type_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    query_type = urllib.parse.parse_qs(parsed.query).get("type", [""])[0].lower()
    if query_type == "video":
        return "video"
    if query_type in {"normal", "image", "note"}:
        return "image"
    return None


def safe_name(value: str, fallback: str = "xhs-note", max_length: int = 100) -> str:
    value = value.strip()[:max_length]
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    return value or fallback


def safe_path_name(value: str, fallback: str = "xhs-note", max_bytes: int = 180) -> str:
    value = safe_name(value, fallback=fallback, max_length=240)
    while len(value.encode("utf-8")) > max_bytes:
        value = value[:-1].strip(" .-_")
    return value or fallback


def note_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, "", [], {}):
            return item[key]
    return None


def note_user(item: dict[str, Any]) -> dict[str, Any]:
    user = note_value(item, "user", "userInfo", "author")
    return user if isinstance(user, dict) else {}


def note_images(item: dict[str, Any]) -> list[Any]:
    images = note_value(item, "images_list", "image_list", "imageList", "images")
    return images if isinstance(images, list) else []


def note_tags(item: dict[str, Any]) -> list[Any]:
    tags = note_value(item, "hash_tag", "tag_list", "tagList", "tags")
    return tags if isinstance(tags, list) else []


def nested_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            urls.extend(nested_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(nested_urls(item))
    elif isinstance(value, str):
        if value.startswith(("http://", "https://")):
            urls.append(value)
        urls.extend(match.group(0) for match in re.finditer(r"https?://[^\s\"'<>\)\]]+", value))
    return [url.rstrip(".,;，。") for url in urls]


def first_nested_url(*values: Any) -> str | None:
    for value in values:
        for url in nested_urls(value):
            return url
    return None


def is_video_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    host = parsed.netloc.lower()
    return path.endswith((".mp4", ".mov", ".m4v", ".m3u8")) or "sns-video" in host or "/stream/" in path


def first_media_url(value: Any, kind: str) -> str | None:
    seen: set[str] = set()
    for url in nested_urls(value):
        if url in seen:
            continue
        seen.add(url)
        if kind == "video" and is_video_url(url):
            return url
        if kind == "image" and is_media_url(url) and not is_video_url(url):
            return url
    return None


def normalize_note_item(item: dict[str, Any]) -> dict[str, Any]:
    note_card = item.get("noteCard")
    return note_card if isinstance(note_card, dict) else item


def note_item_id(item: dict[str, Any]) -> str:
    candidate = normalize_note_item(item)
    value = note_value(item, "id", "note_id", "noteId") or note_value(
        candidate,
        "id",
        "note_id",
        "noteId",
    )
    return str(value or "")


def looks_like_note_item(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return False
    return any(
        item.get(key) not in (None, "", [], {})
        for key in (
            "video_info_v2",
            "videoInfo",
            "video",
            "images_list",
            "image_list",
            "imageList",
            "type",
        )
    )


def build_slug(data: dict[str, Any], source: str) -> str:
    item = find_note_item(data, source) or {}
    user = note_user(item)
    author = (
        user.get("nickname")
        or user.get("name")
        or find_first_by_keys(data, {"nickname", "nick_name", "username", "user_name"})
    )
    title = (
        note_value(item, "title", "display_title", "displayTitle")
        or find_first_by_keys(data, {"title", "display_title", "displaytitle"})
        or note_value(item, "desc", "description")
        or find_first_by_keys(data, {"desc", "description", "content"})
    )
    if author or title:
        return safe_path_name(f"{author or 'unknown'}-{title or 'untitled'}")
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def summarize(data: dict[str, Any], endpoint: str, source: str) -> dict[str, Any]:
    keys = {
        "note_id": {"note_id", "noteid", "id", "noteidstr"},
        "title": {"title", "display_title", "displaytitle"},
        "description": {"desc", "description", "content"},
        "nickname": {"nickname", "nick_name", "username", "user_name"},
        "user_id": {"user_id", "userid", "useridstr", "user_id_str"},
        "liked_count": {"liked_count", "like_count", "likedcount", "likes"},
        "collected_count": {"collected_count", "collect_count", "collectedcount", "collects"},
        "comment_count": {"comment_count", "comments_count", "commentcount", "comments"},
        "share_count": {"share_count", "shared_count", "sharecount", "shares"},
    }
    item = find_note_item(data, source) or {}
    user = note_user(item)
    images = note_images(item)
    hash_tags = note_tags(item)
    summary = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "endpoint": endpoint,
    }
    note_type = item.get("type") if isinstance(item.get("type"), str) else None
    if note_type:
        summary["note_type"] = note_type
    for output_key, possible_keys in keys.items():
        found = find_first_by_keys(data, possible_keys)
        if found not in (None, "", [], {}):
            summary[output_key] = found
    author_avatar = first_url(
        user.get("image"),
        user.get("avatar"),
        user.get("avatar_url"),
        user.get("avatarUrl"),
        find_first_by_keys(user, {"image", "avatar", "avatar_url"}),
    )
    if author_avatar:
        summary["author_avatar"] = author_avatar
    if images:
        summary["image_count"] = len(images)
    tags = [
        str(tag.get("name") or tag.get("tag_name") or tag.get("tagName") or "").strip()
        for tag in hash_tags
        if isinstance(tag, dict) and str(tag.get("name") or tag.get("tag_name") or "").strip()
    ]
    if tags:
        summary["hashtags"] = tags
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
        candidate = normalize_note_item(item)
        if note_id and note_item_id(item) == note_id and looks_like_note_item(candidate):
            return candidate
    for item in iter_dicts(data):
        candidate = normalize_note_item(item)
        if looks_like_note_item(candidate):
            return candidate
    return None


def first_url(*values: Any) -> str | None:
    for value in values:
        url = first_nested_url(value)
        if url:
            return url
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
    user = note_user(item)
    video_info = note_value(item, "video_info_v2", "videoInfo", "video") if isinstance(item, dict) else {}
    if not isinstance(video_info, dict):
        video_info = {}

    image_info = video_info.get("image") if isinstance(video_info.get("image"), dict) else {}
    media = video_info.get("media") if isinstance(video_info.get("media"), dict) else {}
    streams = media.get("stream") if isinstance(media.get("stream"), dict) else {}

    result: dict[str, str] = {}
    author_avatar = first_url(
        user.get("image"),
        user.get("avatar"),
        user.get("avatar_url"),
        user.get("avatarUrl"),
        find_first_by_keys(user, {"image", "avatar", "avatar_url"}),
    )
    if author_avatar:
        result["author_avatar"] = author_avatar
    images = note_images(item) if isinstance(item, dict) else None
    first_image = images[0] if isinstance(images, list) and images and isinstance(images[0], dict) else {}
    post_cover = first_url(
        first_image.get("original"),
        first_image.get("url"),
        first_image.get("urlDefault"),
        first_image.get("urlPre"),
        first_image.get("infoList"),
        item.get("share_info", {}).get("image") if isinstance(item.get("share_info"), dict) else None,
        item.get("shareInfo", {}).get("image") if isinstance(item.get("shareInfo"), dict) else None,
    )
    video_thumbnail = first_url(
        image_info.get("thumbnail"),
        image_info.get("thumbnail_dim"),
        image_info.get("first_frame"),
    )
    first_frame = first_url(image_info.get("first_frame"), video_thumbnail)
    video = best_stream_url(streams) or first_media_url(video_info, "video") or first_media_url(item, "video")

    seen_urls: set[str] = set()
    if post_cover:
        result["cover"] = post_cover
        seen_urls.add(post_cover)
    if isinstance(images, list):
        for index, image in enumerate(images, start=1):
            if not isinstance(image, dict):
                continue
            image_url = first_url(
                image.get("original"),
                image.get("url"),
                image.get("urlDefault"),
                image.get("urlPre"),
                image.get("infoList"),
            )
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
    if query_type in {"normal", "note"}:
        return "image"
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


def download_url(
    url: str,
    target_dir: Path,
    index: int,
    timeout: int,
    base_name: str = "",
    retries: int = 0,
    limiter: DownloadRateLimiter | None = None,
) -> Path:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return download_url_once(url, target_dir, index, timeout, base_name, limiter)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_error or CollectError("download failed")


def download_url_once(
    url: str,
    target_dir: Path,
    index: int,
    timeout: int,
    base_name: str = "",
    limiter: DownloadRateLimiter | None = None,
) -> Path:
    if limiter:
        limiter.wait()
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type")
        ext = guess_extension(url, content_type)
        prefix = f"{base_name}-" if base_name else ""
        target = target_dir / f"{prefix}{index:03d}{ext}"
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
        content_type = response.headers.get("Content-Type")
        ext = guess_extension(url, content_type)
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


def download_indexed_media(task: tuple[int, str, Path, str, int, int, DownloadRateLimiter | None]) -> dict[str, Any]:
    index, url, target_dir, base_name, timeout, retries, limiter = task
    name = f"{index:03d}"
    try:
        media_path = download_url(url, target_dir, index, timeout, base_name, retries, limiter)
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


def call_note_api(
    source: str,
    note_type: str,
    token: str,
    base_url: str,
    timeout: int,
    retries: int,
    fallback: bool,
) -> tuple[str, dict[str, Any]]:
    resolved_source = resolve_xhs_short_link(source, timeout) or source
    note_id = extract_note_id(resolved_source) or extract_note_id(source)
    xsec_token = extract_xsec_token(resolved_source) or extract_xsec_token(source)
    params_source = resolved_source if resolved_source != source else source
    params = {"note_id": note_id} if note_id else {"share_text": params_source}

    inferred_type = type_from_url(resolved_source) or infer_note_type(source)
    if note_type == "auto" and inferred_type == "video":
        order = ["video"]
    elif note_type == "auto" and inferred_type == "image":
        order = ["image"]
    elif note_type == "auto":
        order = ["image", "video"]
        if note_id and xsec_token:
            order.append("web_v3")
        order.append("app_v1")
    else:
        order = [note_type]
    if fallback and note_type != "auto" and "app_v1" not in order:
        order.append("app_v1")

    errors: list[str] = []
    for endpoint_name in order:
        path = API_PATHS[endpoint_name]
        url = base_url.rstrip("/") + path
        endpoint_params = dict(params)
        if endpoint_name == "web_v3":
            if not note_id or not xsec_token:
                errors.append("web_v3: note_id and xsec_token are required")
                continue
            endpoint_params = {"note_id": note_id, "xsec_token": xsec_token}
        try:
            data = request_json(url, token, endpoint_params, timeout=timeout, retries=retries)
        except CollectError as exc:
            errors.append(f"{endpoint_name}: {exc}")
            continue

        if looks_successful(data):
            if not has_collectable_note_payload(data, resolved_source):
                errors.append(
                    f"{endpoint_name}: API returned success but note detail payload is empty; "
                    "the note may be unavailable, expired, private, or blocked by xsec_token."
                )
                continue
            incomplete_reason = incomplete_note_payload_reason(data, resolved_source)
            if incomplete_reason and note_type == "auto":
                errors.append(f"{endpoint_name}: {incomplete_reason}")
                continue
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
        help="Note type. auto uses URL type hints or the unified Web V3 detail endpoint.",
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
    parser.add_argument("--media-workers", type=int, default=4, help="Parallel media download workers.")
    parser.add_argument("--media-qps", type=float, default=10.0, help="Maximum media download requests per second.")
    return parser.parse_args()


def main() -> int:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

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
    elif args.download_media and media_urls:
        media_dir = note_dir / "media"
        media_dir.mkdir(exist_ok=True)
        limiter = DownloadRateLimiter(args.media_qps)
        tasks = [
            (index, url, media_dir, slug, args.timeout, args.retries, limiter)
            for index, url in enumerate(media_urls, start=1)
        ]
        with ThreadPoolExecutor(max_workers=max(1, args.media_workers)) as pool:
            results = list(pool.map(download_indexed_media, tasks))
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
                        media_name=None,
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
    completion = media_completion(primary, note_dir / "media", media_errors) if args.download_media else {
        "complete": not bool(primary),
        "expected": list(primary.keys()),
        "present": [],
        "missing": [],
        "blocking_errors": [],
    }
    if args.download_media:
        local_media_path = note_dir / f"{slug}-local_media.json"
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
        "directory": str(note_dir),
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
