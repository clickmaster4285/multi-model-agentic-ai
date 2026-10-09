"""Shared helpers for document tools: templates + image embeds."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src.artifacts import resolve_job_file
from src.config import Config

TEMPLATES = frozenset({"default", "report", "one_pager", "pitch"})
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def template_name(args: dict[str, Any]) -> str:
    raw = str(args.get("template") or "default").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ("onepager", "one_page", "onepage"):
        raw = "one_pager"
    if raw in ("deck", "pitch_deck"):
        raw = "pitch"
    return raw if raw in TEMPLATES else "default"


def subtitle_line(args: dict[str, Any], template: str) -> str:
    sub = str(args.get("subtitle") or "").strip()
    if sub:
        return sub
    if template == "report":
        return f"Report · {date.today().isoformat()}"
    if template == "one_pager":
        return "One-pager"
    if template == "pitch":
        return "Pitch"
    return ""


def resolve_embed_images(
    config: Config, job_id: str, args: dict[str, Any]
) -> tuple[list[Path], list[str]]:
    """Return (found_paths, missing_or_rejected_names)."""
    names: list[str] = []
    single = args.get("image")
    if single:
        names.append(str(single))
    imgs = args.get("images")
    if isinstance(imgs, list):
        names.extend(str(x) for x in imgs if x)
    out: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for name in names[:8]:
        key = Path(name).name
        if not key or key in seen:
            continue
        seen.add(key)
        if Path(key).suffix.lower() not in IMAGE_EXTS:
            missing.append(key)
            continue
        try:
            path = resolve_job_file(config, job_id, key)
        except FileNotFoundError:
            missing.append(key)
            continue
        out.append(path)
    return out, missing

def section_limit(template: str) -> int:
    if template == "one_pager":
        return 6
    if template == "pitch":
        return 10
    return 40
