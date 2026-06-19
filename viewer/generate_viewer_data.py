#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLLECT_DIR = ROOT / "采集文件夹"
OUT = Path(__file__).resolve().parent / "data.js"


def rel(path: Path) -> str:
    return os.path.relpath(path.resolve(), OUT.parent.resolve()).replace(os.sep, "/")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".m4a"}
    )


def cover_for(summary: dict, media: list[Path]) -> Path | None:
    for token in ("-cover.", "-post_cover."):
        for path in media:
            if token in path.name:
                return path
    for path in media:
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return path
    return None


def detect_platform(summary: dict) -> str:
    source = str(summary.get("source", "")).lower()
    if "xiaohongshu" in source or "xhslink" in source:
        return "xhs"
    if "douyin" in source:
        return "douyin"
    if summary.get("aweme_id"):
        return "douyin"
    return "xhs"


def build_item(directory: Path) -> dict | None:
    summary_path = find_one(directory, "-summary.json")
    primary_path = find_one(directory, "-primary_media.json")
    if not summary_path:
        return None

    summary = load_json(summary_path)
    primary = load_json(primary_path) if primary_path else {}
    media = media_files(directory)
    cover = cover_for(summary, media)
    videos = [path for path in media if path.suffix.lower() == ".mp4"]
    images = [path for path in media if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    audio = [path for path in media if path.suffix.lower() == ".m4a"]

    return {
        "name": directory.name,
        "platform": detect_platform(summary),
        "title": summary.get("title") or directory.name,
        "author": summary.get("nickname") or "未知作者",
        "description": summary.get("description") or summary.get("title") or "",
        "liked": summary.get("liked_count"),
        "collected": summary.get("collected_count"),
        "comments": summary.get("comment_count"),
        "shares": summary.get("share_count"),
        "source": summary.get("source"),
        "summaryPath": rel(summary_path),
        "primaryPath": rel(primary_path) if primary_path else "",
        "directory": rel(directory),
        "cover": rel(cover) if cover else "",
        "video": rel(videos[0]) if videos else "",
        "audio": rel(audio[0]) if audio else "",
        "images": [rel(path) for path in images],
        "primary": primary,
    }


def main() -> None:
    items = []
    for directory in sorted(path for path in COLLECT_DIR.iterdir() if path.is_dir()):
        item = build_item(directory)
        if item:
            items.append(item)
    OUT.write_text(
        "window.COLLECTED_ITEMS = "
        + json.dumps(items, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUT} ({len(items)} items)")


if __name__ == "__main__":
    main()
