#!/usr/bin/env python3
from __future__ import annotations

import json
import base64
import hmac
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
VIEWER_DIR = ROOT / "viewer"
COLLECT_DIR = ROOT / "采集文件夹"
SEARCH_CACHE_DIR = ROOT / "搜索缓存"
SEARCH_HISTORY = SEARCH_CACHE_DIR / "search_history.json"
COLLECT_TASK_CACHE_DIR = ROOT / "任务缓存"
COLLECT_TASK_STORE = COLLECT_TASK_CACHE_DIR / "collect_tasks.json"
DATA_JS = VIEWER_DIR / "data.js"
GENERATOR = VIEWER_DIR / "generate_viewer_data.py"
COLLECTORS = {
    "xhs": ROOT / "xhs-tikhub-collector" / "xhs_collect.py",
    "douyin": ROOT / "douyin-tikhub-collector" / "douyin_collect.py",
}
COLLECT_TASKS: dict[str, dict[str, Any]] = {}
COLLECT_TASK_ORDER: list[str] = []
COLLECT_TASK_QUEUE: queue.Queue[str] = queue.Queue()
COLLECT_TASK_LOCK = threading.Lock()
SEARCH_CACHE_LOCK = threading.RLock()
DATA_GENERATE_LOCK = threading.Lock()
COLLECT_WORKERS_STARTED = 0
MAX_COLLECT_TASKS = 80
MAX_COLLECT_WORKERS = max(1, min(10, int(os.environ.get("LINK_COLLECT_WORKERS", "2") or "2")))
MEDIA_QPS_LIMIT = max(0.1, float(os.environ.get("LINK_MEDIA_QPS", "10") or "10"))
MEDIA_QPS_PER_TASK = MEDIA_QPS_LIMIT / MAX_COLLECT_WORKERS
MEDIA_WORKERS_PER_TASK = max(1, min(10, int(os.environ.get("LINK_MEDIA_WORKERS_PER_TASK", str(max(1, int(MEDIA_QPS_PER_TASK)))) or "1")))
SAFE_VIEWER_SUFFIXES = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
SAFE_MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".m4a", ".mp3", ".bin"}
APP_APK_PATH = "/downloads/social-media-claw-debug.apk"
APP_APK_URL = os.environ.get("LINK_APP_DOWNLOAD_URL")
APP_APK_SHA256 = os.environ.get("LINK_APP_APK_SHA256", "")
TIKHUB_ENV_FILE = os.environ.get("LINK_TIKHUB_ENV_FILE")
TIKHUB_BASE_URL = os.environ.get("TIKHUB_BASE_URL", "https://api.tikhub.io")
SEARCH_CACHE_TTL_MS = 21 * 24 * 60 * 60 * 1000


def auth_config() -> tuple[str, str] | None:
    password = os.environ.get("LINK_VIEWER_PASSWORD")
    if not password:
        return None
    return os.environ.get("LINK_VIEWER_USER", "admin"), password


def json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def app_download_url(handler: SimpleHTTPRequestHandler) -> str:
    if APP_APK_URL:
        return APP_APK_URL

    host = handler.headers.get("Host")
    if not host:
        return APP_APK_PATH

    proto = "https" if os.environ.get("LINK_VIEWER_PUBLIC_HTTPS") == "1" else "http"
    return f"{proto}://{host}{APP_APK_PATH}"


def local_apk_sha256() -> str:
    if APP_APK_SHA256:
        return APP_APK_SHA256
    apk_path = resolve_child(ROOT / "downloads", remove_prefix(APP_APK_PATH, "/downloads/"))
    if not apk_path or not apk_path.is_file():
        return ""
    import hashlib

    digest = hashlib.sha256()
    with apk_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def app_version(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    apk_url = app_download_url(handler)
    return {
        "ok": True,
        "apiVersion": 2,
        "features": ["collect_tasks", "search", "partial_collect_status", "apk_sha256"],
        "latestVersionCode": int(os.environ.get("LINK_APP_LATEST_VERSION_CODE", "13")),
        "latestVersionName": os.environ.get("LINK_APP_LATEST_VERSION_NAME", "1.12"),
        "minSupportedVersionCode": int(os.environ.get("LINK_APP_MIN_VERSION_CODE", "13")),
        "forceUpdate": True,
        "downloadUrl": apk_url,
        "apkUrl": apk_url,
        "apkSha256": local_apk_sha256(),
        "title": os.environ.get("LINK_APP_UPDATE_TITLE", "发现新版本"),
        "message": os.environ.get(
            "LINK_APP_UPDATE_MESSAGE",
            "当前版本需要更新后继续使用，已为你准备好最新安装包。",
        ),
    }


def run_command(args: list[str], cwd: Path, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def ensure_data() -> list[dict[str, Any]]:
    with DATA_GENERATE_LOCK:
        result = run_command([sys.executable, str(GENERATOR)], cwd=VIEWER_DIR, timeout=60)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "生成 data.js 失败").strip())
        return read_items()


def read_items() -> list[dict[str, Any]]:
    if not DATA_JS.exists():
        return []
    text = DATA_JS.read_text(encoding="utf-8")
    match = re.match(r"\s*window\.COLLECTED_ITEMS\s*=\s*(.*?);\s*$", text, re.S)
    if not match:
        return []
    data = json.loads(match.group(1))
    return data if isinstance(data, list) else []


def detect_platform(source: str, requested: str) -> str:
    if requested in COLLECTORS:
        return requested
    lowered = source.lower()
    if any(token in lowered for token in ("xiaohongshu.com", "xhslink.com", "xhs.cn", "小红书")):
        return "xhs"
    if any(token in lowered for token in ("douyin.com", "iesdouyin.com", "v.douyin.com", "抖音")):
        return "douyin"
    raise ValueError("无法判断链接平台，请选择小红书或抖音后再采集。")


def collect_error_message(message: str) -> str:
    if "Missing API key" in message or "TIKHUB_API_KEY" in message:
        return "采集服务还没配置 TIKHUB_API_KEY。请在服务器环境变量或采集脚本 .env 里设置 TikHub API Key 后重试。"
    return message


def tikhub_env_file() -> Path | None:
    candidates = []
    if TIKHUB_ENV_FILE:
        candidates.append(Path(TIKHUB_ENV_FILE).expanduser())
    candidates.extend(
        [
            ROOT / ".env",
            ROOT / "xhs-tikhub-collector" / ".env",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_dotenv_values(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tikhub_token() -> str:
    env_file = tikhub_env_file()
    if env_file:
        load_dotenv_values(env_file)
    token = os.environ.get("TIKHUB_API_KEY") or os.environ.get("TIKHUB_TOKEN")
    if not token:
        raise RuntimeError("采集服务还没配置 TIKHUB_API_KEY。请在服务器环境变量或采集脚本 .env 里设置 TikHub API Key 后重试。")
    return token


def request_tikhub_json(method: str, path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = tikhub_token()
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
    url = TIKHUB_BASE_URL.rstrip("/") + path
    if query:
        url += "?" + query
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "social-media-claw/1.0",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read().decode(charset, errors="replace")
            return json.loads(text)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TikHub HTTP {exc.code}: {detail[:800]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"TikHub 请求失败：{exc}") from exc


def api_success(data: dict[str, Any]) -> bool:
    code = data.get("code")
    if code in {0, 200} or str(code).lower() in {"0", "200", "success"}:
        return True
    message = str(data.get("message") or data.get("msg") or "").lower()
    return bool(data.get("data")) and not any(word in message for word in ("error", "fail", "invalid"))


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def first_value(value: Any, *keys: str) -> Any:
    lower_keys = {key.lower() for key in keys}
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in lower_keys and item not in (None, "", [], {}):
                return item
        for item in value.values():
            found = first_value(item, *keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for item in value:
            found = first_value(item, *keys)
            if found not in (None, "", [], {}):
                return found
    return None


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
        urls.extend(match.group(0).rstrip(".,;，。") for match in re.finditer(r"https?://[^\s\"'<>\)\]]+", value))
    return urls


def first_url(*values: Any) -> str:
    for value in values:
        urls = nested_urls(value)
        if urls:
            return urls[0]
    return ""


def first_image_url(*values: Any) -> str:
    for url in [candidate for value in values for candidate in nested_urls(value)]:
        lowered = url.lower()
        if any(token in lowered for token in (".jpg", ".jpeg", ".png", ".webp", "sns-img", "byteimg", "douyinpic", "xhscdn")):
            return url
    return first_url(*values)


def first_video_url(*values: Any) -> str:
    for url in [candidate for value in values for candidate in nested_urls(value)]:
        lowered = url.lower()
        if any(token in lowered for token in (".mp4", ".m3u8", "sns-video", "douyinvod", "playwm", "play_addr")):
            return url
    return ""


def intish(value: Any) -> int:
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return int(float(value))
    except Exception:
        return 0


def normalize_search_platform(value: str) -> str:
    value = (value or "all").strip().lower()
    return value if value in {"all", "xhs", "douyin"} else "all"


def normalize_search_content_type(value: str) -> str:
    value = (value or "all").strip().lower()
    aliases = {"image": "image", "images": "image", "picture": "image", "article": "article", "video": "video", "all": "all"}
    return aliases.get(value, "all")


def xhs_sort(value: str, fallback: bool = False) -> str:
    mapping = {
        "all": "general",
        "general": "general",
        "latest": "time_descending",
        "new": "time_descending",
        "time": "time_descending",
        "likes": "popularity_descending",
        "hot": "popularity_descending",
        "comments": "comment_descending",
        "collects": "collect_descending",
        "collected": "collect_descending",
    }
    sort = mapping.get((value or "general").strip().lower(), "general")
    if fallback and sort not in {"general", "time_descending", "popularity_descending"}:
        return "general"
    return sort


def xhs_note_type(content_type: str, fallback: bool = False) -> str:
    if fallback:
        return {"video": "1", "image": "2"}.get(content_type, "0")
    return {"video": "视频笔记", "image": "普通笔记"}.get(content_type, "不限")


def xhs_publish_time(value: str) -> str:
    mapping = {
        "day": "一天内",
        "1": "一天内",
        "week": "一周内",
        "7": "一周内",
        "half_year": "半年内",
        "180": "半年内",
    }
    return mapping.get((value or "all").strip().lower(), "不限")


def douyin_sort(value: str) -> str:
    mapping = {
        "all": "0",
        "general": "0",
        "likes": "1",
        "hot": "1",
        "latest": "2",
        "new": "2",
        "time": "2",
    }
    return mapping.get((value or "all").strip().lower(), "0")


def douyin_publish_time(value: str) -> str:
    mapping = {"day": "1", "1": "1", "week": "7", "7": "7", "half_year": "180", "180": "180"}
    return mapping.get((value or "all").strip().lower(), "0")


def douyin_duration(value: str) -> str:
    mapping = {"short": "0-1", "0-1": "0-1", "medium": "1-5", "1-5": "1-5", "long": "5-10000", "5-10000": "5-10000"}
    return mapping.get((value or "all").strip().lower(), "0")


def douyin_content_type(value: str) -> str:
    mapping = {"video": "1", "image": "2", "article": "3"}
    return mapping.get(value, "0")


def result_source(platform: str, item: dict[str, Any]) -> str:
    if platform == "xhs":
        share = first_url(item.get("share_info"), item.get("shareInfo"), item.get("share_url"), item.get("url"), item.get("link"))
        if share:
            return share
        note_id = str(item.get("note_id") or item.get("noteId") or item.get("id") or "")
        xsec_token = str(item.get("xsec_token") or item.get("xsecToken") or first_value(item, "xsec_token", "xsecToken") or "")
        if note_id:
            query = f"?xsec_token={urllib.parse.quote(xsec_token)}" if xsec_token else ""
            return f"https://www.xiaohongshu.com/explore/{note_id}{query}"
        return ""
    share = first_url(item.get("share_url"), item.get("shareUrl"), item.get("share_info"), item.get("url"))
    if share:
        return share
    aweme_id = str(item.get("aweme_id") or item.get("awemeId") or item.get("item_id") or item.get("id") or "")
    return f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""


def normalize_xhs_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    item = raw.get("note_card") or raw.get("noteCard") or raw.get("note") or raw
    if not isinstance(item, dict):
        return None
    note_id = str(raw.get("id") or raw.get("note_id") or item.get("id") or item.get("note_id") or "")
    user = item.get("user") or item.get("user_info") or item.get("userInfo") or raw.get("user") or {}
    interact = item.get("interact_info") or item.get("interactInfo") or raw.get("interact_info") or {}
    note_type = str(item.get("type") or raw.get("type") or "")
    cover = first_image_url(
        item.get("cover"),
        item.get("image_list"),
        item.get("imageList"),
        item.get("images_list"),
        item.get("images"),
        item.get("video_info"),
        item.get("videoInfo"),
        raw,
    )
    video = first_video_url(item.get("video_info"), item.get("videoInfo"), item)
    content_type = "video" if video or "video" in note_type.lower() else "image"
    source = result_source("xhs", {**item, "id": note_id})
    title = str(item.get("display_title") or item.get("title") or item.get("desc") or raw.get("title") or "").strip()
    if not note_id and not title and not cover:
        return None
    return {
        "id": note_id or hashlib_id(source or title),
        "platform": "xhs",
        "contentType": content_type,
        "isVideo": content_type == "video",
        "title": title or "小红书笔记",
        "description": str(item.get("desc") or item.get("description") or title or ""),
        "author": str(user.get("nickname") or user.get("name") or item.get("nickname") or "未知作者"),
        "avatar": first_image_url(user.get("avatar"), user.get("image"), user.get("avatar_url"), user),
        "cover": cover,
        "video": video,
        "source": source,
        "liked": intish(interact.get("liked_count") or interact.get("like_count") or item.get("liked_count")),
        "collected": intish(interact.get("collected_count") or interact.get("collect_count") or item.get("collected_count")),
        "comments": intish(interact.get("comment_count") or item.get("comment_count")),
        "publishedAt": str(item.get("time") or item.get("last_update_time") or ""),
        "raw": raw,
    }


def normalize_douyin_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    item = raw.get("aweme_info") or raw.get("aweme") or raw.get("aweme_detail") or raw
    if not isinstance(item, dict):
        return None
    if not (item.get("aweme_id") or item.get("video") or item.get("images") or item.get("image_infos")):
        return None
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    video_info = item.get("video") if isinstance(item.get("video"), dict) else {}
    images = item.get("images") or item.get("image_infos") or []
    cover = first_image_url(
        video_info.get("cover"),
        video_info.get("origin_cover"),
        video_info.get("dynamic_cover"),
        images,
        item,
    )
    video = first_video_url(video_info.get("play_addr"), video_info.get("download_addr"), video_info, item.get("video_url"))
    is_image = isinstance(images, list) and len(images) > 0
    content_type = "image" if is_image and not video else "video"
    source = result_source("douyin", item)
    title = str(item.get("desc") or item.get("title") or raw.get("title") or "").strip()
    aweme_id = str(item.get("aweme_id") or item.get("id") or "")
    return {
        "id": aweme_id or hashlib_id(source or title),
        "platform": "douyin",
        "contentType": content_type,
        "isVideo": content_type == "video",
        "title": title or "抖音作品",
        "description": title,
        "author": str(author.get("nickname") or author.get("unique_id") or "未知作者"),
        "avatar": first_image_url(author.get("avatar_thumb"), author.get("avatar_medium"), author.get("avatar_larger"), author),
        "cover": cover,
        "video": video,
        "source": source,
        "liked": intish(statistics.get("digg_count") or item.get("digg_count")),
        "collected": intish(statistics.get("collect_count") or item.get("collect_count")),
        "comments": intish(statistics.get("comment_count") or item.get("comment_count")),
        "publishedAt": str(item.get("create_time") or ""),
        "raw": raw,
    }


def hashlib_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def candidate_result_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[Any] = []
    for key in ("items", "notes", "feeds", "aweme_list", "data", "list", "detail"):
        value = first_value(data, key)
        if isinstance(value, list):
            containers.append(value)
    if isinstance(data.get("data"), list):
        containers.append(data["data"])
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for container in containers:
        for item in container:
            if isinstance(item, dict) and id(item) not in seen:
                seen.add(id(item))
                result.append(item)
    if not result:
        for item in iter_dicts(data):
            if item is not data and id(item) not in seen:
                seen.add(id(item))
                result.append(item)
    return result


def normalize_results(platform: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidate_result_items(data):
        item = normalize_xhs_item(raw) if platform == "xhs" else normalize_douyin_item(raw)
        if not item:
            continue
        key = f"{item['platform']}:{item['id']}:{item.get('source')}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= 40:
            break
    return normalized


def public_search_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "raw"}


def public_search_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_search_item(item) for item in items if isinstance(item, dict)]


def call_xhs_search(params: dict[str, Any]) -> dict[str, Any]:
    keyword = str(params["keyword"])
    page = intish(params.get("page")) or 1
    content_type = normalize_search_content_type(str(params.get("contentType") or "all"))
    query = {
        "keyword": keyword,
        "page": page,
        "sort": xhs_sort(str(params.get("sort") or "general")),
        "note_type": xhs_note_type(content_type),
        "publish_time": xhs_publish_time(str(params.get("publishTime") or "all")),
        "ai_mode": "0",
    }
    search_id = str(params.get("searchId") or "")
    search_session_id = str(params.get("searchSessionId") or "")
    if search_id:
        query["search_id"] = search_id
    if search_session_id:
        query["search_session_id"] = search_session_id
    errors: list[str] = []
    try:
        data = request_tikhub_json("GET", "/api/v1/xiaohongshu/app_v2/search_notes", query)
        if api_success(data):
            return {"endpoint": "xhs_app_v2", "raw": data, "items": normalize_results("xhs", data)}
        errors.append(str(data.get("message") or data.get("msg") or data.get("code")))
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    fallback_query = {
        "keyword": keyword,
        "page": page,
        "sort": xhs_sort(str(params.get("sort") or "general"), fallback=True),
        "note_type": xhs_note_type(content_type, fallback=True),
    }
    data = request_tikhub_json("GET", "/api/v1/xiaohongshu/web_v3/fetch_search_notes", fallback_query)
    if not api_success(data):
        raise RuntimeError("; ".join(errors + [str(data.get("message") or data.get("msg") or data.get("code"))]))
    return {"endpoint": "xhs_web_v3", "raw": data, "items": normalize_results("xhs", data), "degraded": True}


def call_douyin_search(params: dict[str, Any]) -> dict[str, Any]:
    content_type = normalize_search_content_type(str(params.get("contentType") or "all"))
    body = {
        "keyword": str(params["keyword"]),
        "cursor": intish(params.get("cursor")),
        "sort_type": douyin_sort(str(params.get("sort") or "general")),
        "publish_time": douyin_publish_time(str(params.get("publishTime") or "all")),
        "filter_duration": douyin_duration(str(params.get("duration") or "all")),
        "content_type": douyin_content_type(content_type),
        "search_id": str(params.get("searchId") or ""),
        "backtrace": str(params.get("backtrace") or ""),
    }
    if content_type == "video":
        order = [
            ("douyin_video_v2", "/api/v1/douyin/search/fetch_video_search_v2"),
            ("douyin_general_v1", "/api/v1/douyin/search/fetch_general_search_v1"),
        ]
    elif content_type == "image":
        order = [
            ("douyin_image_v3", "/api/v1/douyin/search/fetch_image_search_v3"),
            ("douyin_image", "/api/v1/douyin/search/fetch_image_search"),
            ("douyin_general_v1", "/api/v1/douyin/search/fetch_general_search_v1"),
        ]
    else:
        order = [
            ("douyin_general_v1", "/api/v1/douyin/search/fetch_general_search_v1"),
            ("douyin_general_v2", "/api/v1/douyin/search/fetch_general_search_v2"),
        ]
    errors: list[str] = []
    for endpoint, path in order:
        try:
            data = request_tikhub_json("POST", path, body=body)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{endpoint}: {exc}")
            continue
        if api_success(data):
            items = normalize_results("douyin", data)
            if content_type == "image":
                items = [item for item in items if item.get("contentType") == "image"]
            elif content_type == "video":
                items = [item for item in items if item.get("contentType") == "video"]
            return {"endpoint": endpoint, "raw": data, "items": items, "degraded": endpoint != order[0][0]}
        errors.append(f"{endpoint}: {data.get('message') or data.get('msg') or data.get('code')}")
    raise RuntimeError("; ".join(errors) or "抖音搜索失败。")


def search_cache_init() -> None:
    with SEARCH_CACHE_LOCK:
        SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not SEARCH_HISTORY.exists():
            atomic_write_text(SEARCH_HISTORY, "[]\n")


def load_search_history() -> list[dict[str, Any]]:
    with SEARCH_CACHE_LOCK:
        search_cache_init()
        try:
            data = json.loads(SEARCH_HISTORY.read_text(encoding="utf-8"))
        except Exception:
            data = []
        history = data if isinstance(data, list) else []
        cutoff = now_ms() - SEARCH_CACHE_TTL_MS
        kept = [item for item in history if intish(item.get("createdAt")) >= cutoff]
        if len(kept) != len(history):
            save_search_history(kept)
        return kept


def save_search_history(history: list[dict[str, Any]]) -> None:
    with SEARCH_CACHE_LOCK:
        search_cache_init()
        atomic_write_text(SEARCH_HISTORY, json.dumps(history[:120], ensure_ascii=False, indent=2) + "\n")


def search_result_path(search_id: str) -> Path:
    safe = re.sub(r"[^0-9a-zA-Z_-]+", "", search_id)
    return SEARCH_CACHE_DIR / f"{safe}.json"


def filters_summary(params: dict[str, Any]) -> str:
    labels = []
    if params.get("platform") and params.get("platform") != "all":
        labels.append("小红书" if params["platform"] == "xhs" else "抖音")
    if params.get("contentType") and params.get("contentType") != "all":
        labels.append({"video": "视频", "image": "图文", "article": "文章"}.get(params["contentType"], str(params["contentType"])))
    if params.get("sort") and params.get("sort") not in {"all", "general"}:
        labels.append({"latest": "最新", "likes": "最多点赞", "comments": "最多评论", "collects": "最多收藏"}.get(params["sort"], str(params["sort"])))
    if params.get("publishTime") and params.get("publishTime") != "all":
        labels.append({"day": "一天内", "week": "一周内", "half_year": "半年内"}.get(params["publishTime"], str(params["publishTime"])))
    return " · ".join(labels) if labels else "默认筛选"


def execute_platform_search(platform: str, params: dict[str, Any]) -> dict[str, Any]:
    if platform == "xhs":
        return call_xhs_search(params)
    if platform == "douyin":
        return call_douyin_search(params)
    raise ValueError("未知搜索平台。")


def search_page_state(platform: str, params: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    if platform == "xhs":
        next_page = (intish(params.get("page")) or 1) + 1
        search_id = str(first_value(raw, "search_id", "searchId") or params.get("searchId") or "")
        session_id = str(first_value(raw, "search_session_id", "searchSessionId") or params.get("searchSessionId") or "")
        has_more_raw = first_value(raw, "has_more", "hasMore", "has_next", "hasNext")
        has_more = bool(items) if has_more_raw in (None, "", [], {}) else str(has_more_raw).lower() not in {"0", "false", "none"}
        state = {"hasMore": has_more, "page": next_page}
        if search_id:
            state["searchId"] = search_id
        if session_id:
            state["searchSessionId"] = session_id
        return state

    cursor = first_value(raw, "cursor", "next_cursor", "nextCursor", "cursor_str", "cursorStr")
    search_id = first_value(raw, "search_id", "searchId")
    backtrace = first_value(raw, "backtrace")
    has_more_raw = first_value(raw, "has_more", "hasMore")
    has_more = bool(items) if has_more_raw in (None, "", [], {}) else str(has_more_raw).lower() not in {"0", "false", "none"}
    state = {"hasMore": has_more}
    if cursor not in (None, "", [], {}):
        state["cursor"] = intish(cursor) if str(cursor).isdigit() else str(cursor)
    if search_id not in (None, "", [], {}):
        state["searchId"] = str(search_id)
    if backtrace not in (None, "", [], {}):
        state["backtrace"] = str(backtrace)
    if "cursor" not in state and has_more:
        state["cursor"] = intish(params.get("cursor")) + len(items)
    return state


def apply_platform_page_state(params: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    next_params = dict(params)
    if not isinstance(state, dict):
        return next_params
    for source_key, target_key in (
        ("page", "page"),
        ("cursor", "cursor"),
        ("searchId", "searchId"),
        ("searchSessionId", "searchSessionId"),
        ("backtrace", "backtrace"),
    ):
        if source_key in state:
            next_params[target_key] = state[source_key]
    return next_params


def merge_search_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in existing + new_items:
        if not isinstance(item, dict):
            continue
        key = f"{item.get('platform')}:{item.get('id')}:{item.get('source')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def execute_search(payload: dict[str, Any]) -> dict[str, Any]:
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("请输入搜索关键词。")
    platform = normalize_search_platform(str(payload.get("platform") or "all"))
    content_type = normalize_search_content_type(str(payload.get("contentType") or "all"))
    params = {
        "keyword": keyword,
        "platform": platform,
        "contentType": content_type,
        "sort": str(payload.get("sort") or "general").strip().lower(),
        "publishTime": str(payload.get("publishTime") or "all").strip().lower(),
        "duration": str(payload.get("duration") or "all").strip().lower(),
        "page": intish(payload.get("page")) or 1,
        "cursor": intish(payload.get("cursor")),
        "searchId": str(payload.get("searchId") or ""),
        "searchSessionId": str(payload.get("searchSessionId") or ""),
        "backtrace": str(payload.get("backtrace") or ""),
    }
    platforms = ["xhs", "douyin"] if platform == "all" else [platform]
    incoming_page_state = payload.get("pageState") if isinstance(payload.get("pageState"), dict) else {}
    platform_results: list[dict[str, Any]] = []
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    next_page = {"hasMore": False, "platforms": {}}
    for target_platform in platforms:
        try:
            platform_params = apply_platform_page_state(params, incoming_page_state.get(target_platform))
            result = execute_platform_search(target_platform, platform_params)
            platform_state = search_page_state(target_platform, platform_params, result)
            next_page["platforms"][target_platform] = platform_state
            if platform_state.get("hasMore"):
                next_page["hasMore"] = True
            platform_results.append(
                {
                    "platform": target_platform,
                    "endpoint": result.get("endpoint"),
                    "count": len(result.get("items") or []),
                    "degraded": bool(result.get("degraded")),
                    "nextPage": platform_state,
                }
            )
            items.extend(result.get("items") or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{target_platform}: {exc}")
    if not items and errors:
        raise RuntimeError("; ".join(errors))

    append_to = str(payload.get("appendTo") or "").strip()
    cached_payload = read_cached_search(append_to) if append_to else None
    search_id = append_to if cached_payload else uuid.uuid4().hex
    created_at = now_ms()
    existing_record = cached_payload.get("record") if isinstance(cached_payload, dict) else None
    page_items = public_search_items(items)
    existing_items = cached_payload.get("items") if isinstance(cached_payload, dict) and isinstance(cached_payload.get("items"), list) else []
    merged_items = merge_search_items(existing_items, page_items) if cached_payload else page_items
    record = {
        "id": search_id,
        "keyword": keyword,
        "platform": platform,
        "contentType": content_type,
        "filters": {
            "sort": params["sort"],
            "publishTime": params["publishTime"],
            "duration": params["duration"],
        },
        "filterSummary": filters_summary(params),
        "resultCount": len(merged_items),
        "createdAt": intish(existing_record.get("createdAt")) if isinstance(existing_record, dict) else created_at,
        "expiresAt": created_at + SEARCH_CACHE_TTL_MS,
    }
    payload_to_cache = {
        "ok": True,
        "cached": False,
        "appended": bool(cached_payload),
        "record": record,
        "items": merged_items,
        "pageItems": page_items,
        "nextPage": next_page,
        "platformResults": platform_results,
        "errors": errors,
    }
    with SEARCH_CACHE_LOCK:
        atomic_write_text(search_result_path(search_id), json.dumps(payload_to_cache, ensure_ascii=False, indent=2))
        if cached_payload:
            history = [record if item.get("id") == search_id else item for item in load_search_history()]
        else:
            history = [record] + [item for item in load_search_history() if item.get("id") != search_id]
        save_search_history(history)
    return payload_to_cache


def read_cached_search(search_id: str) -> dict[str, Any] | None:
    with SEARCH_CACHE_LOCK:
        path = search_result_path(search_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        record = payload.get("record") if isinstance(payload, dict) else None
        if not isinstance(record, dict) or intish(record.get("expiresAt")) < now_ms():
            return None
        payload["cached"] = True
        return payload


def delete_search_history(search_id: str | None = None) -> int:
    with SEARCH_CACHE_LOCK:
        history = load_search_history()
        if search_id:
            next_history = [item for item in history if item.get("id") != search_id]
            path = search_result_path(search_id)
            if path.exists():
                path.unlink()
        else:
            next_history = []
            for item in history:
                path = search_result_path(str(item.get("id") or ""))
                if path.exists():
                    path.unlink()
        save_search_history(next_history)
        return len(history) - len(next_history)


def search_trending() -> list[str]:
    terms: list[str] = []
    try:
        data = request_tikhub_json("GET", "/api/v1/xiaohongshu/web_v3/fetch_trending", {})
        for item in iter_dicts(data):
            for key in ("keyword", "word", "title", "name", "query"):
                value = item.get(key) if isinstance(item, dict) else None
                if isinstance(value, str) and 1 < len(value.strip()) <= 24:
                    terms.append(value.strip())
    except Exception:
        pass
    fallback = ["codex", "AI 工作流", "小红书运营", "抖音热门视频", "自媒体工具", "端午旅行"]
    return list(dict.fromkeys(terms + fallback))[:12]


def collect_source(source: str, platform: str, download_media: bool, content_type: str = "auto") -> dict[str, Any]:
    script = COLLECTORS[platform]
    args = [
        sys.executable,
        str(script),
        source,
        "--out",
        str(COLLECT_DIR),
        "--timeout",
        "45",
        "--retries",
        "1",
        "--media-workers",
        str(MEDIA_WORKERS_PER_TASK),
        "--media-qps",
        f"{MEDIA_QPS_PER_TASK:.3f}",
    ]
    if platform == "xhs" and content_type in {"auto", "image", "video"}:
        args.extend(["--type", content_type])
    env_file = tikhub_env_file()
    if env_file:
        args.extend(["--env-file", str(env_file)])
    if download_media:
        args.append("--download-media")

    result = run_command(args, cwd=script.parent, timeout=300)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "采集失败").strip()
        raise RuntimeError(collect_error_message(message[-1600:]))

    try:
        collector_payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        collector_payload = {"raw_output": result.stdout.strip()}

    items = ensure_data()
    media_complete = collector_payload.get("media_complete")
    missing_media = collector_payload.get("missing_media") if isinstance(collector_payload.get("missing_media"), list) else []
    return {
        "ok": True,
        "platform": platform,
        "mediaComplete": media_complete if isinstance(media_complete, bool) else None,
        "missingMedia": missing_media,
        "collector": collector_payload,
        "items": items,
    }


def now_ms() -> int:
    return int(time.time() * 1000)


def compact_source_title(source: str) -> str:
    compact = re.sub(r"\s+", " ", source).strip()
    return compact[:80] if compact else "采集任务"


def collect_task_public(task: dict[str, Any], include_result: bool = False) -> dict[str, Any]:
    payload = {
        "id": str(task.get("id") or ""),
        "source": str(task.get("source") or ""),
        "platform": str(task.get("platform") or "auto"),
        "contentType": str(task.get("content_type") or "auto"),
        "downloadMedia": bool(task.get("download_media", True)),
        "title": str(task.get("title") or compact_source_title(str(task.get("source") or ""))),
        "status": str(task.get("status") or "queued"),
        "error": task.get("error") or "",
        "mediaComplete": task.get("media_complete"),
        "missingMedia": task.get("missing_media") or [],
        "createdAt": intish(task.get("created_at")),
        "updatedAt": intish(task.get("updated_at")),
        "startedAt": task.get("started_at"),
        "finishedAt": task.get("finished_at"),
    }
    if include_result and task.get("result") is not None:
        payload["result"] = task["result"]
    return payload


def persist_collect_tasks_locked() -> None:
    COLLECT_TASK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        COLLECT_TASKS[task_id]
        for task_id in COLLECT_TASK_ORDER
        if task_id in COLLECT_TASKS
    ]
    atomic_write_text(COLLECT_TASK_STORE, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_collect_tasks() -> None:
    if not COLLECT_TASK_STORE.is_file():
        return
    try:
        data = json.loads(COLLECT_TASK_STORE.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list):
        return
    now = now_ms()
    with COLLECT_TASK_LOCK:
        COLLECT_TASKS.clear()
        COLLECT_TASK_ORDER.clear()
        for raw_task in data[:MAX_COLLECT_TASKS]:
            if not isinstance(raw_task, dict) or not raw_task.get("id"):
                continue
            task = dict(raw_task)
            task.setdefault("source", "")
            task.setdefault("platform", "auto")
            task.setdefault("content_type", "auto")
            task.setdefault("download_media", True)
            task.setdefault("title", compact_source_title(str(task.get("source") or "")))
            task.setdefault("status", "queued")
            task.setdefault("error", "")
            task.setdefault("created_at", now)
            task.setdefault("started_at", None)
            task.setdefault("finished_at", None)
            if task.get("status") in {"queued", "running"}:
                task["status"] = "error"
                task["error"] = "服务重启后任务已中断，请重新提交。"
                task["finished_at"] = now
            task.setdefault("media_complete", None)
            task.setdefault("missing_media", [])
            task["updated_at"] = now
            task_id = str(task["id"])
            COLLECT_TASKS[task_id] = task
            COLLECT_TASK_ORDER.append(task_id)
        persist_collect_tasks_locked()


def trim_collect_tasks() -> None:
    while len(COLLECT_TASK_ORDER) > MAX_COLLECT_TASKS:
        old_id = COLLECT_TASK_ORDER.pop()
        old_task = COLLECT_TASKS.get(old_id)
        if old_task and old_task.get("status") in {"queued", "running"}:
            COLLECT_TASK_ORDER.append(old_id)
            break
        COLLECT_TASKS.pop(old_id, None)


def create_collect_task(source: str, platform: str, download_media: bool, content_type: str = "auto") -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "source": source,
        "platform": platform,
        "content_type": content_type,
        "download_media": download_media,
        "title": compact_source_title(source),
        "status": "queued",
        "error": "",
        "media_complete": None,
        "missing_media": [],
        "result": None,
        "created_at": now_ms(),
        "updated_at": now_ms(),
        "started_at": None,
        "finished_at": None,
    }
    with COLLECT_TASK_LOCK:
        COLLECT_TASKS[task_id] = task
        COLLECT_TASK_ORDER.insert(0, task_id)
        trim_collect_tasks()
        persist_collect_tasks_locked()
    COLLECT_TASK_QUEUE.put(task_id)
    return task


def list_collect_tasks() -> list[dict[str, Any]]:
    with COLLECT_TASK_LOCK:
        return [
            collect_task_public(COLLECT_TASKS[task_id])
            for task_id in COLLECT_TASK_ORDER
            if task_id in COLLECT_TASKS
        ]


def get_collect_task(task_id: str, include_result: bool = False) -> dict[str, Any] | None:
    with COLLECT_TASK_LOCK:
        task = COLLECT_TASKS.get(task_id)
        if not task:
            return None
        return collect_task_public(task, include_result=include_result)


def update_collect_task(task_id: str, **updates: Any) -> None:
    with COLLECT_TASK_LOCK:
        task = COLLECT_TASKS.get(task_id)
        if not task:
            return
        task.update(updates)
        task["updated_at"] = now_ms()
        persist_collect_tasks_locked()


def collect_worker() -> None:
    while True:
        task_id = COLLECT_TASK_QUEUE.get()
        try:
            with COLLECT_TASK_LOCK:
                task = COLLECT_TASKS.get(task_id)
                if not task:
                    continue
                task["status"] = "running"
                task["started_at"] = now_ms()
                task["updated_at"] = now_ms()
                source = task["source"]
                platform = task["platform"]
                download_media = bool(task["download_media"])
                content_type = str(task.get("content_type") or "auto")
            try:
                result = collect_source(source, platform, download_media, content_type)
                media_complete = result.get("mediaComplete")
                missing_media = result.get("missingMedia") if isinstance(result.get("missingMedia"), list) else []
                next_status = "ok"
                if download_media and media_complete is False:
                    next_status = "partial"
                next_error = ""
                if next_status == "partial":
                    next_error = "媒体下载不完整"
                    if missing_media:
                        next_error += "，缺少：" + "、".join(str(item) for item in missing_media)
                update_collect_task(
                    task_id,
                    status=next_status,
                    result=result,
                    error=next_error,
                    media_complete=media_complete,
                    missing_media=missing_media,
                    finished_at=now_ms(),
                )
            except subprocess.TimeoutExpired:
                update_collect_task(
                    task_id,
                    status="error",
                    error="采集超时，请稍后重试。",
                    finished_at=now_ms(),
                )
            except Exception as exc:  # noqa: BLE001
                update_collect_task(task_id, status="error", error=str(exc), finished_at=now_ms())
        finally:
            COLLECT_TASK_QUEUE.task_done()


def ensure_collect_worker() -> None:
    global COLLECT_WORKERS_STARTED
    if COLLECT_WORKERS_STARTED:
        return
    for index in range(MAX_COLLECT_WORKERS):
        thread = threading.Thread(target=collect_worker, name=f"collect-worker-{index + 1}", daemon=True)
        thread.start()
    COLLECT_WORKERS_STARTED = MAX_COLLECT_WORKERS


def resolve_child(base: Path, relative_path: str) -> Path | None:
    decoded = urllib.parse.unquote(relative_path).strip("/")
    if not decoded:
        return None
    target = (base / decoded).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target


def remove_prefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


class Handler(SimpleHTTPRequestHandler):
    server_version = "LinkCollectorViewer/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), format % args))

    def send_static_file(self, path: Path, send_body: bool = True) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            match = re.match(r"bytes=(\d*)-(\d*)$", range_header)
            if match:
                start_text, end_text = match.groups()
                if start_text or end_text:
                    if start_text:
                        start = int(start_text)
                        end = int(end_text) if end_text else file_size - 1
                    else:
                        suffix_length = int(end_text)
                        start = max(0, file_size - suffix_length)
                        end = file_size - 1
                    end = min(end, file_size - 1)
                    if 0 <= start <= end < file_size:
                        length = end - start + 1
                        self.send_response(HTTPStatus.PARTIAL_CONTENT)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(length))
                        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Last-Modified", self.date_time_string(path.stat().st_mtime))
                        self.end_headers()
                        if not send_body:
                            return
                        with path.open("rb") as handle:
                            handle.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk = handle.read(min(1024 * 256, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                        return
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(path.stat().st_mtime))
        self.end_headers()
        if not send_body:
            return
        with path.open("rb") as handle:
            self.copyfile(handle, self.wfile)

    def send_not_found(self) -> None:
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def is_authorized(self) -> bool:
        expected = auth_config()
        if not expected:
            return True

        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False

        username, separator, password = decoded.partition(":")
        if not separator:
            return False

        expected_user, expected_password = expected
        return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)

    def require_auth(self) -> None:
        body = json.dumps({"ok": False, "error": "Unauthorized"}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Link Collector"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_static_get(self, send_body: bool = True) -> None:
        clean_path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if clean_path == "/api/app-version":
            json_response(self, HTTPStatus.OK, app_version(self))
            return
        if clean_path == "/healthz":
            if not self.is_authorized():
                self.require_auth()
                return
            json_response(self, HTTPStatus.OK, {"ok": True})
            return
        if clean_path.startswith("/downloads/"):
            target = resolve_child(ROOT / "downloads", remove_prefix(clean_path, "/downloads/"))
            if target and target.suffix.lower() == ".apk":
                self.send_static_file(target, send_body=send_body)
                return
            self.send_not_found()
            return

        if clean_path.startswith("/采集文件夹/"):
            target = resolve_child(COLLECT_DIR, remove_prefix(clean_path, "/采集文件夹/"))
            if not target:
                self.send_not_found()
                return
            relative_parts = target.relative_to(COLLECT_DIR.resolve()).parts
            is_media = len(relative_parts) >= 3 and relative_parts[-2] == "media"
            is_summary = target.name.endswith("-summary.json")
            is_primary = target.name.endswith("-primary_media.json")
            if is_media and target.suffix.lower() in SAFE_MEDIA_SUFFIXES:
                self.send_static_file(target, send_body=send_body)
                return
            if not self.is_authorized():
                self.require_auth()
                return
            if is_summary or is_primary:
                self.send_static_file(target, send_body=send_body)
                return
            self.send_not_found()
            return

        if not self.is_authorized():
            self.require_auth()
            return
        if clean_path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/viewer/")
            self.end_headers()
            return
        if clean_path == "/api/items":
            try:
                items = ensure_data()
                json_response(self, HTTPStatus.OK, {"ok": True, "items": items})
            except Exception as exc:  # noqa: BLE001
                json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        if clean_path == "/api/collect-tasks":
            json_response(self, HTTPStatus.OK, {"ok": True, "tasks": list_collect_tasks()})
            return
        if clean_path == "/api/collect-task":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            task_id = str((query.get("id") or [""])[0]).strip()
            task = get_collect_task(task_id, include_result=True)
            if not task:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "采集任务不存在。"})
                return
            json_response(self, HTTPStatus.OK, {"ok": True, "task": task})
            return
        if clean_path == "/api/search-history":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            platform = normalize_search_platform(str((query.get("platform") or ["all"])[0]))
            history = load_search_history()
            if platform != "all":
                history = [item for item in history if item.get("platform") in {platform, "all"}]
            json_response(self, HTTPStatus.OK, {"ok": True, "history": history})
            return
        if clean_path == "/api/search-result":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            search_id = str((query.get("id") or [""])[0]).strip()
            payload = read_cached_search(search_id)
            if not payload:
                json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "搜索缓存不存在或已过期。"})
                return
            json_response(self, HTTPStatus.OK, payload)
            return
        if clean_path == "/api/search-trending":
            json_response(self, HTTPStatus.OK, {"ok": True, "terms": search_trending()})
            return

        if clean_path in {"/viewer", "/viewer/"}:
            self.send_static_file(VIEWER_DIR / "index.html", send_body=send_body)
            return
        if clean_path.startswith("/viewer/"):
            target = resolve_child(VIEWER_DIR, remove_prefix(clean_path, "/viewer/"))
            if target and target.suffix.lower() in SAFE_VIEWER_SUFFIXES:
                self.send_static_file(target, send_body=send_body)
                return
            self.send_not_found()
            return

        self.send_not_found()

    def do_GET(self) -> None:
        self.handle_static_get(send_body=True)

    def do_HEAD(self) -> None:
        self.handle_static_get(send_body=False)

    def do_POST(self) -> None:
        if not self.is_authorized():
            self.require_auth()
            return

        clean_path = self.path.split("?", 1)[0]
        if clean_path not in {"/api/collect", "/api/search"}:
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 50_000:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "请求内容为空或过大。"})
            return

        if clean_path == "/api/search":
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = execute_search(payload)
            except Exception as exc:  # noqa: BLE001
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            json_response(self, HTTPStatus.OK, result)
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            source = str(payload.get("source") or "").strip()
            requested_platform = str(payload.get("platform") or "auto").strip().lower()
            content_type = str(payload.get("contentType") or "auto").strip().lower()
            download_media = bool(payload.get("downloadMedia", True))
            if not source:
                raise ValueError("请先粘贴链接或分享文本。")
            platform = detect_platform(source, requested_platform)
            if content_type not in {"auto", "image", "video"}:
                content_type = "auto"
            if platform != "xhs":
                content_type = "auto"
        except Exception as exc:  # noqa: BLE001
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        task = create_collect_task(source, platform, download_media, content_type)
        json_response(
            self,
            HTTPStatus.ACCEPTED,
            {"ok": True, "queued": True, "task": collect_task_public(task)},
        )

    def do_DELETE(self) -> None:
        if not self.is_authorized():
            self.require_auth()
            return
        clean_path = self.path.split("?", 1)[0]
        if clean_path != "/api/search-history":
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        search_id = str((query.get("id") or [""])[0]).strip() or None
        deleted = delete_search_history(search_id)
        json_response(self, HTTPStatus.OK, {"ok": True, "deleted": deleted})


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LINK_VIEWER_HOST", "127.0.0.1")
    load_collect_tasks()
    ensure_data()
    ensure_collect_worker()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving dynamic collector on http://{host}:{port}/viewer/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
