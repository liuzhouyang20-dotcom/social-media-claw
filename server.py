#!/usr/bin/env python3
from __future__ import annotations

import json
import base64
import hmac
import mimetypes
import os
import re
import subprocess
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
VIEWER_DIR = ROOT / "viewer"
COLLECT_DIR = ROOT / "采集文件夹"
DATA_JS = VIEWER_DIR / "data.js"
GENERATOR = VIEWER_DIR / "generate_viewer_data.py"
COLLECTORS = {
    "xhs": ROOT / "xhs-tikhub-collector" / "xhs_collect.py",
    "douyin": ROOT / "douyin-tikhub-collector" / "douyin_collect.py",
}
COLLECT_LOCK = threading.Lock()
SAFE_VIEWER_SUFFIXES = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
SAFE_MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".m4a"}
APP_APK_PATH = "/downloads/social-media-claw-debug.apk"
APP_APK_URL = os.environ.get("LINK_APP_DOWNLOAD_URL")
TIKHUB_ENV_FILE = os.environ.get("LINK_TIKHUB_ENV_FILE")


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


def app_download_url(handler: SimpleHTTPRequestHandler) -> str:
    if APP_APK_URL:
        return APP_APK_URL

    forwarded_proto = handler.headers.get("X-Forwarded-Proto")
    forwarded_host = handler.headers.get("X-Forwarded-Host")
    host = forwarded_host or handler.headers.get("Host")
    if not host:
        return APP_APK_PATH

    proto = forwarded_proto or "http"
    return f"{proto}://{host}{APP_APK_PATH}"


def app_version(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    apk_url = app_download_url(handler)
    return {
        "ok": True,
        "latestVersionCode": int(os.environ.get("LINK_APP_LATEST_VERSION_CODE", "4")),
        "latestVersionName": os.environ.get("LINK_APP_LATEST_VERSION_NAME", "1.3"),
        "minSupportedVersionCode": int(os.environ.get("LINK_APP_MIN_VERSION_CODE", "4")),
        "forceUpdate": True,
        "downloadUrl": apk_url,
        "apkUrl": apk_url,
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


def collect_source(source: str, platform: str, download_media: bool) -> dict[str, Any]:
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
    ]
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
    return {
        "ok": True,
        "platform": platform,
        "collector": collector_payload,
        "items": items,
    }


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
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
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
            target = resolve_child(ROOT / "downloads", clean_path.removeprefix("/downloads/"))
            if target and target.suffix.lower() == ".apk":
                self.send_static_file(target, send_body=send_body)
                return
            self.send_not_found()
            return

        if clean_path.startswith("/采集文件夹/"):
            target = resolve_child(COLLECT_DIR, clean_path.removeprefix("/采集文件夹/"))
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

        if clean_path in {"/viewer", "/viewer/"}:
            self.send_static_file(VIEWER_DIR / "index.html", send_body=send_body)
            return
        if clean_path.startswith("/viewer/"):
            target = resolve_child(VIEWER_DIR, clean_path.removeprefix("/viewer/"))
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
        if clean_path != "/api/collect":
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 50_000:
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": "请求内容为空或过大。"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            source = str(payload.get("source") or "").strip()
            requested_platform = str(payload.get("platform") or "auto").strip().lower()
            download_media = bool(payload.get("downloadMedia", True))
            if not source:
                raise ValueError("请先粘贴链接或分享文本。")
            platform = detect_platform(source, requested_platform)
        except Exception as exc:  # noqa: BLE001
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        if not COLLECT_LOCK.acquire(blocking=False):
            json_response(self, HTTPStatus.CONFLICT, {"ok": False, "error": "已有采集任务在运行，请稍后再试。"})
            return
        try:
            result = collect_source(source, platform, download_media)
            json_response(self, HTTPStatus.OK, result)
        except subprocess.TimeoutExpired:
            json_response(self, HTTPStatus.REQUEST_TIMEOUT, {"ok": False, "error": "采集超时，请稍后重试。"})
        except Exception as exc:  # noqa: BLE001
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
        finally:
            COLLECT_LOCK.release()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    host = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("LINK_VIEWER_HOST", "127.0.0.1")
    ensure_data()
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
