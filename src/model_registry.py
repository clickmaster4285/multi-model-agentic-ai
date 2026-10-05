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
    if name.endswith(":cloud"):
        return "cloud"
    if classify_vram(name) == "small":
        return "fast"
    if classify_vram(name) == "large":
        return "strong"
    return "general"


def sync_models_from_ollama(session: Session, config: Config | None = None) -> list[ModelRecord]:
    config = config or Config.from_env()
    client = LLMClient(config)
    try:
        tags = client.list_tags()
    except Exception:
        tags = []

    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for item in tags:
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        seen.add(name)
        existing = session.scalar(select(ModelRecord).where(ModelRecord.name == name))
        backend = "remote_openai_compatible" if name.endswith(":cloud") else "local_ollama"
        if existing:
            existing.last_seen_at = now
            existing.backend = backend
            existing.vram_class = classify_vram(name)
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
                    last_seen_at=now,
                )
            )

    # Ensure configured role models exist even if temporarily missing from tags
    for name, role in (
        (config.model_fast, "fast"),
        (config.model_strong, "strong"),
        (config.model_cloud, "cloud"),
    ):
        if not name:
            continue
        existing = session.scalar(select(ModelRecord).where(ModelRecord.name == name))
        if existing:
            existing.role = role
            existing.enabled = True
        elif name not in seen:
            session.add(
                ModelRecord(
                    name=name,
                    backend="remote_openai_compatible" if name.endswith(":cloud") else "local_ollama",
                    vram_class=classify_vram(name),
                    role=role,
                    max_concurrency=1,
                    enabled=True,
                    last_seen_at=now,
                )
            )

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

    fast = pick("fast", config.model_fast or config.llm_model)
    strong = pick("strong", config.model_strong or config.llm_model)
    cloud = pick("cloud", config.model_cloud)

    if override_model:
        return {
            "panel": override_model,
            "consensus": override_model,
            "planner": override_model,
            "worker": override_model,
            "critic": override_model,
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
        "overflow": cloud or "",
        "overflow_applied": "1" if use_cloud else "0",
    }
