"""Safe job-scoped artifact files (docs, html, office, zip) for download."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.config import Config

SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")

MIME_BY_EXT: dict[str, str] = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

MAX_TEXT_CHARS = 400_000
MAX_FILE_BYTES = 25 * 1024 * 1024


def safe_filename(name: str, *, default: str = "artifact.txt") -> str:
    raw = Path(str(name or default)).name.strip() or default
    stem = SAFE_NAME.sub("_", Path(raw).stem)[:80] or "artifact"
    ext = Path(raw).suffix.lower()
    if not ext or ext not in MIME_BY_EXT:
        ext = Path(default).suffix.lower() or ".txt"
        if ext not in MIME_BY_EXT:
            ext = ".txt"
    return f"{stem}{ext}"


def mime_for(filename: str) -> str:
    return MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


def job_dir(config: Config, job_id: str) -> Path:
    path = Path(config.artifacts_dir) / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_files_dir(config: Config, job_id: str) -> Path:
    path = job_dir(config, job_id) / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_job_file(config: Config, job_id: str, filename: str) -> Path:
    """Resolve a file inside the job artifacts tree (no path traversal)."""
    root = job_dir(config, job_id).resolve()
    name = Path(str(filename or "")).name
    if not name:
        raise FileNotFoundError("empty filename")
    # Prefer files/ then images/ then job root
    candidates = [
        root / "files" / name,
        root / "images" / name,
        root / name,
    ]
    for path in candidates:
        resolved = path.resolve()
        if root not in resolved.parents and resolved != root:
            continue
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(name)


def write_bytes(
    config: Config,
    job_id: str,
    filename: str,
    data: bytes,
    *,
    kind: str = "document",
) -> dict[str, Any]:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File too large (max {MAX_FILE_BYTES // (1024 * 1024)}MB)")
    name = safe_filename(filename)
    path = job_files_dir(config, job_id) / name
    path.write_bytes(data)
    meta = {
        "filename": name,
        "original": filename,
        "mime": mime_for(name),
        "relpath": f"{job_id}/files/{name}",
        "bytes": len(data),
        "kind": kind,
    }
    _append_manifest(config, job_id, meta)
    return meta


def write_text(
    config: Config,
    job_id: str,
    filename: str,
    content: str,
    *,
    kind: str = "document",
) -> dict[str, Any]:
    text = content if len(content) <= MAX_TEXT_CHARS else content[:MAX_TEXT_CHARS]
    return write_bytes(config, job_id, filename, text.encode("utf-8"), kind=kind)


def list_artifacts(config: Config, job_id: str) -> list[dict[str, Any]]:
    manifest = job_dir(config, job_id) / "artifacts.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    # Fallback: scan files/
    out: list[dict[str, Any]] = []
    files = job_files_dir(config, job_id)
    for path in sorted(files.iterdir()):
        if path.is_file():
            out.append(
                {
                    "filename": path.name,
                    "mime": mime_for(path.name),
                    "relpath": f"{job_id}/files/{path.name}",
                    "bytes": path.stat().st_size,
                    "kind": "document",
                }
            )
    return out


def _append_manifest(config: Config, job_id: str, meta: dict[str, Any]) -> None:
    path = job_dir(config, job_id) / "artifacts.json"
    rows = list_artifacts(config, job_id)
    rows = [r for r in rows if r.get("filename") != meta.get("filename")]
    rows.append(meta)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def artifact_event(meta: dict[str, Any], *, job_id: str) -> dict[str, Any]:
    return {
        "type": "artifact_ready",
        "job_id": job_id,
        "filename": meta.get("filename"),
        "mime": meta.get("mime"),
        "relpath": meta.get("relpath"),
        "bytes": meta.get("bytes"),
        "kind": meta.get("kind") or "document",
    }
