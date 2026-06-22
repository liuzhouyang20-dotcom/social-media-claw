#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server
import viewer.generate_viewer_data as viewer_data


XHS_SPEC = importlib.util.spec_from_file_location(
    "xhs_collect_test",
    ROOT / "xhs-tikhub-collector" / "xhs_collect.py",
)
assert XHS_SPEC and XHS_SPEC.loader
xhs_collect = importlib.util.module_from_spec(XHS_SPEC)
XHS_SPEC.loader.exec_module(xhs_collect)


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def test_public_search_items() -> None:
    items = server.public_search_items([{"id": "1", "title": "ok", "raw": {"token": "secret"}}])
    assert_true(items == [{"id": "1", "title": "ok"}], "search items should not expose raw payload")


def test_apk_sha() -> None:
    digest = server.local_apk_sha256()
    assert_true(len(digest) in {0, 64}, "APK sha256 should be empty or a hex digest")


def test_collect_task_restore() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        original_store = server.COLLECT_TASK_STORE
        original_cache_dir = server.COLLECT_TASK_CACHE_DIR
        original_tasks = dict(server.COLLECT_TASKS)
        original_order = list(server.COLLECT_TASK_ORDER)
        try:
            server.COLLECT_TASK_CACHE_DIR = Path(tmp)
            server.COLLECT_TASK_STORE = Path(tmp) / "collect_tasks.json"
            server.COLLECT_TASK_STORE.write_text(
                json.dumps(
                    [
                        {
                            "id": "task-1",
                            "source": "https://example.test/item",
                            "platform": "xhs",
                            "content_type": "auto",
                            "download_media": True,
                            "title": "restore-test",
                            "status": "running",
                            "error": "",
                            "media_complete": None,
                            "missing_media": [],
                            "result": None,
                            "created_at": 1,
                            "updated_at": 1,
                            "started_at": 1,
                            "finished_at": None,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            server.load_collect_tasks()
            task = server.COLLECT_TASKS["task-1"]
            assert_true(task["status"] == "error", "running task should be marked interrupted after restart")
            assert_true("中断" in task["error"], "interrupted task should explain restart")
        finally:
            server.COLLECT_TASK_STORE = original_store
            server.COLLECT_TASK_CACHE_DIR = original_cache_dir
            server.COLLECT_TASKS.clear()
            server.COLLECT_TASKS.update(original_tasks)
            server.COLLECT_TASK_ORDER[:] = original_order


def test_collect_task_public_legacy_shape() -> None:
    payload = server.collect_task_public({"id": "legacy-1", "status": "queued"})
    assert_true(payload["contentType"] == "auto", "legacy task should default content type")
    assert_true(payload["downloadMedia"] is True, "legacy task should default media download")
    assert_true(payload["createdAt"] == 0 and payload["updatedAt"] == 0, "legacy timestamps should be numeric")


def test_viewer_data_media_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        collect_dir = root / "collect"
        out = root / "viewer" / "data.js"
        audit = root / "viewer" / "media_audit.json"
        item_dir = collect_dir / "author-title"
        media_dir = item_dir / "media"
        media_dir.mkdir(parents=True)
        out.parent.mkdir(parents=True)

        original_collect_dir = viewer_data.COLLECT_DIR
        original_out = viewer_data.OUT
        original_audit = viewer_data.AUDIT_OUT
        try:
            viewer_data.COLLECT_DIR = collect_dir
            viewer_data.OUT = out
            viewer_data.AUDIT_OUT = audit
            (item_dir / "author-title-summary.json").write_text(
                json.dumps({"title": "title", "nickname": "author", "source": "https://www.xiaohongshu.com/explore/1"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (item_dir / "author-title-primary_media.json").write_text(
                json.dumps({"cover": "https://example.test/cover.jpg", "video": "https://example.test/video.mp4"}),
                encoding="utf-8",
            )
            (media_dir / "author-title-cover.jpg").write_bytes(b"fake")
            item = viewer_data.build_item(item_dir)
            assert_true(item is not None, "viewer generator should build item")
            assert_true(item["mediaComplete"] is False, "missing video should mark item partial")
            assert_true(item["missingMedia"] == ["video"], "missing media keys should be reported")

            (media_dir / "author-title-video.mp4").write_bytes(b"fake")
            item = viewer_data.build_item(item_dir)
            assert_true(item["mediaComplete"] is True, "all primary media files should mark item complete")

            out.write_text('window.COLLECTED_ITEMS = [{"title":"keep"}];\n', encoding="utf-8")
            for child in item_dir.iterdir():
                if child.is_file():
                    child.unlink()
            viewer_data.main()
            assert_true('window.COLLECTED_ITEMS = []' in out.read_text(encoding="utf-8"), "empty collect dir should rewrite data.js as empty")
            assert_true(audit.exists(), "empty collect dir should still write an audit file")
            assert_true(audit.read_text(encoding="utf-8").strip() == "[]", "empty collect dir audit should be empty")
        finally:
            viewer_data.COLLECT_DIR = original_collect_dir
            viewer_data.OUT = original_out
            viewer_data.AUDIT_OUT = original_audit


def test_collect_task_status_without_media() -> None:
    task = server.create_collect_task("https://example.test/item", "xhs", "auto")
    try:
        queued = server.COLLECT_TASKS[task["id"]]
        queued["status"] = "ok"
        queued["media_complete"] = False
        queued["missing_media"] = ["video"]
        public = server.collect_task_public(queued)
        assert_true(public["status"] == "ok", "task without media download should remain ok when complete")
    finally:
        server.COLLECT_TASKS.pop(task["id"], None)
        server.COLLECT_TASK_ORDER[:] = [task_id for task_id in server.COLLECT_TASK_ORDER if task_id != task["id"]]


def test_xhs_auto_prefers_app_v2_then_web_v3() -> None:
    source = "https://www.xiaohongshu.com/explore/6a30f896000000000f01c67f?xsec_token=abc123&xsec_source=pc_search&source=web_search_result_notes"
    calls: list[str] = []

    original_request_json = xhs_collect.request_json
    try:
        def fake_request_json(url: str, token: str, params: dict[str, str], timeout: int, retries: int) -> dict:
            calls.append(url)
            if "app_v2/get_image_note_detail" in url or "app_v2/get_video_note_detail" in url:
                return {"code": 200, "data": {}}
            if "web_v3/fetch_note_detail" in url:
                return {
                    "code": 200,
                    "data": {
                        "type": "normal",
                        "noteId": "6a30f896000000000f01c67f",
                        "title": "test",
                        "desc": "test",
                        "user": {"nickname": "tester"},
                        "imageList": [{"original": "https://example.test/a.jpg"}],
                    },
                }
            return {"code": 200, "data": {}}

        xhs_collect.request_json = fake_request_json  # type: ignore[assignment]
        endpoint, data = xhs_collect.call_note_api(
            source=source,
            note_type="auto",
            token="dummy",
            base_url="https://api.tikhub.io",
            timeout=1,
            retries=0,
            fallback=True,
        )
        assert_true(endpoint == "web_v3", "auto flow should fall back to web_v3 after app_v2")
        assert_true(calls[0].endswith("/app_v2/get_image_note_detail"), "auto flow should try app_v2 first")
        assert_true(bool(xhs_collect.find_note_item(data, source)), "web_v3 fallback should return a note item")
    finally:
        xhs_collect.request_json = original_request_json  # type: ignore[assignment]


def main() -> int:
    test_public_search_items()
    test_apk_sha()
    test_collect_task_restore()
    test_collect_task_public_legacy_shape()
    test_viewer_data_media_status()
    test_collect_task_status_without_media()
    test_xhs_auto_prefers_app_v2_then_web_v3()
    print("backend smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
