"""Validate and persist chat image attachments for vision models."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from src.config import Config

ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGES = 4
MAX_BYTES = 5 * 1024 * 1024
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def strip_data_url(raw: str) -> tuple[str | None, str]:
    text = (raw or "").strip()
    mime = None
    if text.startswith("data:"):
        header, _, rest = text.partition(",")
        match = re.match(r"data:([^;]+)", header)
        if match:
            mime = match.group(1).lower()
        text = rest
    return mime, re.sub(r"\s+", "", text)


def decode_image(item: dict[str, Any]) -> tuple[str, str, bytes]:
    mime_hint = str(item.get("mime") or "").lower().strip()
    filename = str(item.get("filename") or "image.png")
    data_raw = str(item.get("data") or "")
    parsed_mime, b64 = strip_data_url(data_raw)
    mime = (parsed_mime or mime_hint or "image/png").split(";")[0].strip().lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in ALLOWED_MIME:
        raise ValueError(f"Unsupported image type: {mime}")
    try:
        blob = base64.b64decode(b64, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Invalid image data") from exc
    if not blob:
        raise ValueError("Empty image")
    if len(blob) > MAX_BYTES:
        raise ValueError(f"Image too large (max {MAX_BYTES // (1024 * 1024)}MB)")
    return filename, mime, blob


def save_images(config: Config, job_id: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not items:
        return []
    if len(items) > MAX_IMAGES:
        raise ValueError(f"At most {MAX_IMAGES} images per message")
    folder = Path(config.artifacts_dir) / job_id / "images"
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for i, item in enumerate(items, start=1):
        filename, mime, blob = decode_image(item)
        ext = ALLOWED_MIME[mime]
        stem = SAFE_NAME.sub("_", Path(filename).stem)[:40] or f"image{i}"
        stored = f"{i:02d}_{stem}{ext}"
        path = folder / stored
        path.write_bytes(blob)
        saved.append(
            {
                "filename": stored,
                "original": filename,
                "mime": mime,
                "relpath": f"{job_id}/images/{stored}",
            }
        )
    return saved


def image_path(config: Config, relpath: str) -> Path:
    root = Path(config.artifacts_dir).resolve()
    path = (root / relpath).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid attachment path")
    if not path.is_file():
        raise FileNotFoundError(relpath)
    return path


def load_b64_images(config: Config, attachments: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for item in attachments:
        rel = str(item.get("relpath") or "")
        if not rel:
            continue
        blob = image_path(config, rel).read_bytes()
        out.append(base64.b64encode(blob).decode("ascii"))
    return out
