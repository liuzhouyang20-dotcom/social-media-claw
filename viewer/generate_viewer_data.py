#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLLECT_DIR = ROOT / "采集文件夹"
OUT = Path(__file__).resolve().parent / "data.js"
AUDIT_OUT = Path(__file__).resolve().parent / "media_audit.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_SUFFIXES = {".mp4"}
AUDIO_SUFFIXES = {".m4a", ".mp3"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES
COVER_TOKENS = ("-cover.", "-post_cover.", "-video_thumbnail.", "-first_frame.", "-dynamic_cover.")


def rel(path: Path) -> str:
    return os.path.relpath(path.resolve(), OUT.parent.resolve()).replace(os.sep, "/")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_text(path: Path, text: str) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def existing_item_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
        prefix = "window.COLLECTED_ITEMS = "
        start = text.index(prefix) + len(prefix)
        end = text.rfind(";")
        if end <= start:
            return 0
        data = json.loads(text[start:end].strip())
    except Exception:  # noqa: BLE001
        return 0
    return len(data) if isinstance(data, list) else 0


def load_json_optional(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:  # noqa: BLE001
        return {}


def find_one(directory: Path, suffix: str) -> Path | None:
    matches = sorted(directory.glob(f"*{suffix}"))
    return matches[0] if matches else None


def media_files(directory: Path) -> list[Path]:
    media_dir = directory / "media"
    if not media_dir.exists():
        return []
    return sorted(
        path
        for path in media_dir.iterdir()
        if path.suffix.lower() in MEDIA_SUFFIXES
    )


def cover_for(media: list[Path]) -> Path | None:
    for token in COVER_TOKENS:
        for path in media:
            if token in path.name:
                return path
    for path in media:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            name = path.name.lower()
            if "author_avatar" in name or "avatar" in name:
                continue
            return path
    return None


def local_avatar_for(media: list[Path]) -> Path | None:
    for path in media:
        if "-author_avatar." in path.name:
            return path
    return None


def local_media_for_key(media: list[Path], key: str) -> Path | None:
    token = f"-{key}."
    for path in media:
        if token in path.name:
            return path
    return None


def local_keys(primary: dict, media: list[Path]) -> set[str]:
    return {key for key in primary if local_media_for_key(media, key)}


def local_record(directory: Path) -> dict:
    return load_json_optional(find_one(directory, "-local_media.json"))


def media_errors(record: dict) -> list[dict]:
    errors = record.get("media_errors")
    return errors if isinstance(errors, list) else []


def missing_media_keys(primary: dict, media: list[Path]) -> list[str]:
    present = local_keys(primary, media)
    return [key for key in primary if key not in present]


def media_status(primary: dict, media: list[Path], record: dict) -> dict:
    missing = missing_media_keys(primary, media)
    errors = media_errors(record)
    missing_set = set(missing)
    blocking_errors = [
        error for error in errors
        if isinstance(error, dict) and error.get("name") in missing_set
    ]
    if not primary:
        state = "no-primary-media"
    elif not missing:
        state = "complete"
    else:
        state = "partial"
    return {
        "state": state,
        "complete": state == "complete",
        "expected": list(primary.keys()),
        "present": sorted(local_keys(primary, media)),
        "missing": missing,
        "errors": errors,
        "blockingErrors": blocking_errors,
        "hasLocalMediaRecord": bool(record),
        "files": [path.name for path in media],
    }


def raw_note_type(directory: Path) -> str:
    raw_path = find_one(directory, "-raw.json")
    if not raw_path:
        return ""
    try:
        raw = load_json(raw_path)
    except Exception:  # noqa: BLE001
        return ""

    def iter_dicts(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from iter_dicts(item)
        elif isinstance(value, list):
            for item in value:
                yield from iter_dicts(item)

    best = None
    best_score = -1
    for item in iter_dicts(raw):
        score = sum(1 for key in ("id", "title", "desc", "user", "images_list", "type") if item.get(key))
        if score > best_score:
            best = item
            best_score = score
    if isinstance(best, dict) and isinstance(best.get("type"), str):
        note_type = best["type"]
        if note_type in {"normal", "note"}:
            return "image"
        return note_type
    return ""


def detect_platform(summary: dict) -> str:
    source = str(summary.get("source", "")).lower()
    if "xiaohongshu" in source or "xhslink" in source:
        return "xhs"
    if "douyin" in source:
        return "douyin"
    if summary.get("aweme_id"):
        return "douyin"
    return "xhs"


def normalize_content_type(value: str) -> str:
    if value in {"normal", "note"}:
        return "image"
    return value


def build_item(directory: Path) -> dict | None:
    summary_path = find_one(directory, "-summary.json")
    primary_path = find_one(directory, "-primary_media.json")
    if not summary_path:
        return None

    summary = load_json(summary_path)
    primary = load_json_optional(primary_path)
    media = media_files(directory)
    videos = [path for path in media if path.suffix.lower() in VIDEO_SUFFIXES]
    images = [
        path for path in media
        if path.suffix.lower() in IMAGE_SUFFIXES
        and "author_avatar" not in path.name.lower()
        and "avatar" not in path.name.lower()
    ]
    audio = [path for path in media if path.suffix.lower() in AUDIO_SUFFIXES]
    cover = cover_for(media)
    avatar = local_avatar_for(media)
    note_type = normalize_content_type(summary.get("note_type") or raw_note_type(directory))
    status = media_status(primary, media, local_record(directory))

    return {
        "name": directory.name,
        "platform": detect_platform(summary),
        "title": summary.get("title") or directory.name,
        "author": summary.get("nickname") or "未知作者",
        "authorId": summary.get("user_id") or "",
        "avatar": rel(avatar) if avatar else "",
        "contentType": note_type or ("video" if videos else "image"),
        "isVideo": note_type == "video" or bool(videos),
        "description": summary.get("description") or summary.get("title") or "",
        "liked": summary.get("liked_count"),
        "collected": summary.get("collected_count"),
        "comments": summary.get("comment_count"),
        "shares": summary.get("share_count"),
        "imageCount": summary.get("image_count"),
        "hashtags": summary.get("hashtags") or [],
        "source": summary.get("source"),
        "summaryPath": rel(summary_path),
        "primaryPath": rel(primary_path) if primary_path else "",
        "directory": rel(directory),
        "cover": rel(cover) if cover else "",
        "video": rel(videos[0]) if videos else "",
        "audio": rel(audio[0]) if audio else "",
        "images": [rel(path) for path in images],
        "mediaStatus": status,
        "missingMedia": status["missing"],
        "mediaComplete": status["complete"],
        "primary": primary,
    }


def main() -> None:
    COLLECT_DIR.mkdir(parents=True, exist_ok=True)

    items = []
    for directory in sorted(path for path in COLLECT_DIR.iterdir() if path.is_dir()):
        item = build_item(directory)
        if item:
            items.append(item)
    if not items and existing_item_count(OUT) > 0:
        print(f"No collected items found; kept existing {OUT}")
        return
    audit = [
        {
            "name": item["name"],
            "title": item["title"],
            "platform": item["platform"],
            "contentType": item["contentType"],
            "mediaComplete": item["mediaComplete"],
            "expected": item["mediaStatus"]["expected"],
            "present": item["mediaStatus"]["present"],
            "missing": item["missingMedia"],
            "errors": item["mediaStatus"]["blockingErrors"],
            "files": item["mediaStatus"]["files"],
        }
        for item in items
    ]
    atomic_write_text(
        OUT,
        "window.COLLECTED_ITEMS = "
        + json.dumps(items, ensure_ascii=False, indent=2)
        + ";\n"
    )
    atomic_write_text(AUDIT_OUT, json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {OUT} ({len(items)} items)")
    print(f"Wrote {AUDIT_OUT}")


if __name__ == "__main__":
    main()
