"""Discover and route models across local/cloud backends."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import Config
from src.llm_client import LLMClient
from src.models_db import ModelRecord


def classify_vram(name: str) -> str:
    lower = name.lower()
    if any(x in lower for x in ("1.7b", "3b", "0.5b", "mini", "tiny")):
        return "small"
    if any(x in lower for x in ("70b", "34b", "32b", "cloud", "405b", "397b")):
        return "large"
    return "medium"


def infer_role(name: str, config: Config) -> str:
    if name == config.model_fast:
        return "fast"
    if name == config.model_strong:
        return "strong"
    if config.model_cloud and name == config.model_cloud:
        return "cloud"
    if config.model_vision and name == config.model_vision:
        return "general"
    if name.endswith(":cloud"):
        return "cloud"
    if classify_vram(name) == "small":
        return "fast"
    if classify_vram(name) == "large":
        return "strong"
    return "general"


from src.image_gen import looks_like_image_gen_model


def detect_vision(details: dict[str, Any] | None) -> bool:
    if not details:
        return False
    caps = [str(c).lower() for c in (details.get("capabilities") or [])]
    if "vision" in caps:
        return True
    info = details.get("model_info") or {}
    if isinstance(info, dict) and any("vision" in str(k).lower() for k in info):
        return True
    return bool(details.get("projector_info"))


def detect_image_gen(details: dict[str, Any] | None, name: str = "") -> bool:
    if looks_like_image_gen_model(name):
        return True
    if not details:
        return False
    caps = [str(c).lower() for c in (details.get("capabilities") or [])]
    return any(c in {"image", "images", "image_generation", "diffusion"} for c in caps)


def _name_looks_like_vision(name: str) -> bool:
    lower = name.lower()
    return "vision" in lower or "llava" in lower or "moondream" in lower or "minicpm-v" in lower


def sync_models_from_ollama(
    session: Session,
    config: Config | None = None,
    *,
    probe_details: bool = False,
) -> list[ModelRecord]:
    """Refresh registry from Ollama `/api/tags`.

    Light sync (default) is enough to add/remove names at runtime.
    `probe_details=True` also hits `/api/show` for vision capabilities.
    If Ollama is unreachable, existing rows are left unchanged.
    """
    config = config or Config.from_env()
    client = LLMClient(config)
    try:
        tags = client.list_tags()
    except Exception:
        return list(session.scalars(select(ModelRecord).order_by(ModelRecord.name)).all())

    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for item in tags:
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        seen.add(name)
        existing = session.scalar(select(ModelRecord).where(ModelRecord.name == name))
        backend = "remote_openai_compatible" if name.endswith(":cloud") else "local_ollama"
        vision = _name_looks_like_vision(name)
        image_gen = looks_like_image_gen_model(name)
        details = None
        if probe_details or existing is None:
            try:
                details = client.show_model(name)
                vision = detect_vision(details)
                image_gen = detect_image_gen(details, name)
            except Exception:
                vision = _name_looks_like_vision(name)
                image_gen = looks_like_image_gen_model(name)
        elif existing is not None:
            vision = bool(getattr(existing, "vision", False)) or vision
            image_gen = bool(getattr(existing, "image_gen", False)) or image_gen
        if existing:
            existing.last_seen_at = now
            existing.backend = backend
            existing.vram_class = classify_vram(name)
            existing.vision = vision
            existing.image_gen = image_gen
            existing.available = True
            if existing.role == "general":
                existing.role = infer_role(name, config)
        else:
            session.add(
                ModelRecord(
                    name=name,
                    backend=backend,
                    vram_class=classify_vram(name),
                    role=infer_role(name, config),
                    max_concurrency=2 if classify_vram(name) == "small" else 1,
                    enabled=True,
                    vision=vision,
                    image_gen=image_gen,
                    available=True,
                    last_seen_at=now,
                )
            )

    for name, role in (
        (config.model_fast, "fast"),
        (config.model_strong, "strong"),
        (config.model_cloud, "cloud"),
        (config.model_vision, "general"),
    ):
        if not name:
            continue
        existing = session.scalar(select(ModelRecord).where(ModelRecord.name == name))
        if existing:
            if role != "general":
                existing.role = role
            existing.enabled = True
            if existing.name in seen or existing.name.endswith(":cloud"):
                existing.available = True

    for row in session.scalars(select(ModelRecord)).all():
        if row.name in seen:
            continue
        if row.name.endswith(":cloud") or row.backend == "remote_openai_compatible":
            row.available = True
            continue
        row.available = False

    session.flush()
    return list(session.scalars(select(ModelRecord).order_by(ModelRecord.name)).all())


def list_models(session: Session, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    stmt = select(ModelRecord).order_by(ModelRecord.role, ModelRecord.name)
    if enabled_only:
        stmt = stmt.where(ModelRecord.enabled.is_(True))
    rows = session.scalars(stmt).all()
    return [
        {
            "name": r.name,
            "backend": r.backend,
            "vram_class": r.vram_class,
            "role": r.role,
            "max_concurrency": r.max_concurrency,
            "enabled": r.enabled,
            "vision": bool(getattr(r, "vision", False)),
            "image_gen": bool(getattr(r, "image_gen", False)),
            "available": bool(getattr(r, "available", True)),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]


def resolve_model_plan(
    session: Session,
    config: Config,
    *,
    override_model: str | None = None,
    queue_wait_seconds: float = 0.0,
    prefer_cloud_overflow: bool = True,
) -> dict[str, str]:
    """Return models for fast/panel, strong/consensus, and optional cloud overflow."""
    enabled = {
        r.name: r
        for r in session.scalars(select(ModelRecord).where(ModelRecord.enabled.is_(True))).all()
        if getattr(r, "available", True)
    }

    def pick(role: str, fallback: str) -> str:
        for row in enabled.values():
            if row.role == role:
                return row.name
        if fallback in enabled:
            return fallback
        if enabled:
            return next(iter(enabled))
        return fallback or config.llm_model

    def pick_vision() -> str:
        preferred = (config.model_vision or "").strip()
        if preferred and preferred in enabled and getattr(enabled[preferred], "vision", False):
            return preferred
        for row in enabled.values():
            if getattr(row, "vision", False):
                return row.name
        if preferred:
            return preferred
        return fast

    fast = pick("fast", config.model_fast or config.llm_model)
    strong = pick("strong", config.model_strong or config.llm_model)
    cloud = pick("cloud", config.model_cloud)
    vision = pick_vision()

    def pick_image() -> str:
        if override_model:
            row = enabled.get(override_model)
            if row is not None and getattr(row, "image_gen", False):
                return override_model
            if looks_like_image_gen_model(override_model):
                return override_model
        preferred = (getattr(config, "model_image", "") or "").strip()
        if preferred and preferred in enabled and getattr(enabled[preferred], "image_gen", False):
            return preferred
        for row in enabled.values():
            if getattr(row, "image_gen", False):
                return row.name
        return preferred

    image = pick_image()

    if override_model:
        override_vision = vision
        row = enabled.get(override_model)
        if row is not None and getattr(row, "vision", False):
            override_vision = override_model
        return {
            "panel": override_model,
            "consensus": override_model,
            "planner": override_model,
            "worker": override_model,
            "critic": override_model,
            "vision": override_vision,
            "image": image,
            "overflow": cloud or override_model,
        }

    use_cloud = (
        prefer_cloud_overflow
        and cloud
        and queue_wait_seconds >= config.overflow_wait_seconds
    )
    panel = cloud if use_cloud else fast
    consensus = cloud if use_cloud else strong
    return {
        "panel": panel,
        "consensus": consensus,
        "planner": panel,
        "worker": consensus,
        "critic": consensus,
        "vision": vision,
        "image": image,
        "overflow": cloud or "",
        "overflow_applied": "1" if use_cloud else "0",
    }


def force_vision_model(
    session: Session,
    config: Config,
    plan: dict[str, str],
    *,
    override_model: str | None = None,
) -> tuple[dict[str, str], str]:
    """When the user attached images, pin the job onto a vision-capable model."""
    enabled = {
        r.name: r
        for r in session.scalars(select(ModelRecord).where(ModelRecord.enabled.is_(True))).all()
        if getattr(r, "available", True)
    }
    vision = (plan.get("vision") or config.model_vision or config.llm_model).strip()
    if override_model:
        row = enabled.get(override_model)
        if row is not None and getattr(row, "vision", False):
            vision = override_model
    if vision in enabled and not getattr(enabled[vision], "vision", False):
        for row in enabled.values():
            if getattr(row, "vision", False):
                vision = row.name
                break
    pinned = {**plan, "vision": vision, "worker": vision}
    return pinned, vision
