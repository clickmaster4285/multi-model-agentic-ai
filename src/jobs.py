"""Job create / claim / event helpers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.config import Config
from src.intent_router import classify_query
from src.model_registry import resolve_model_plan
from src.models_db import Job, JobEvent, User


ACTIVE = ("queued", "running")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _not_deleted():
    return Job.deleted_at.is_(None)


def queue_depth(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "queued", _not_deleted())
        )
        or 0
    )


def count_active_for_user(session: Session, user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.user_id == user_id, Job.status.in_(ACTIVE), _not_deleted())
        )
        or 0
    )


def count_jobs_today(session: Session, user_id: int) -> int:
    start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.user_id == user_id, Job.created_at >= start)
        )
        or 0
    )


def estimate_wait_seconds(session: Session, config: Config) -> float:
    depth = queue_depth(session)
    # Rough heuristic: ~45s per queued job / llm slot
    return depth * 45.0 / max(1, config.llm_slots)


def create_job(
    session: Session,
    *,
    user: User,
    query: str,
    mode: str,
    payload: dict[str, Any],
    config: Config | None = None,
    priority: int = 100,
) -> Job:
    config = config or Config.from_env()
    requested_mode = (mode or "auto").strip().lower()
    if requested_mode not in {"auto", "debate", "agentic", "mixed", "chat"}:
        raise ValueError("mode must be auto, chat, debate, agentic, or mixed")
    if count_active_for_user(session, user.id) >= config.max_active_jobs_per_user:
        raise ValueError(
            f"User already has {config.max_active_jobs_per_user} active job(s). Wait or cancel."
        )
    if queue_depth(session) >= config.queue_depth_limit:
        raise ValueError(
            f"Queue is full ({config.queue_depth_limit}). Try again shortly."
        )
    if count_jobs_today(session, user.id) >= user.daily_job_quota:
        raise ValueError(f"Daily job quota reached ({user.daily_job_quota}).")

    route = classify_query(query.strip(), requested_mode=requested_mode, config=config)
    resolved_mode = route.resolved_mode
    enriched_payload = {
        **payload,
        "requested_mode": requested_mode,
        "route": route.as_dict(),
    }

    wait = estimate_wait_seconds(session, config)
    model_plan = resolve_model_plan(
        session,
        config,
        override_model=payload.get("model"),
        queue_wait_seconds=wait,
        prefer_cloud_overflow=bool(payload.get("allow_overflow", True)),
    )

    job = Job(
        id=str(uuid.uuid4()),
        user_id=user.id,
        mode=resolved_mode,
        status="queued",
        priority=priority,
        query=query.strip(),
        payload_json=json.dumps(enriched_payload),
        model_plan_json=json.dumps(model_plan),
    )
    session.add(job)
    session.flush()
    append_event(
        session,
        job.id,
        {
            "type": "job_queued",
            "job_id": job.id,
            "mode": resolved_mode,
            "requested_mode": requested_mode,
            "estimated_wait_seconds": wait,
            "model_plan": model_plan,
            "queue_depth": queue_depth(session),
        },
    )
    append_event(
        session,
        job.id,
        {
            "type": "route_decided",
            **route.as_dict(),
        },
    )
    return job


def append_event(session: Session, job_id: str, event: dict[str, Any]) -> JobEvent:
    last_seq = session.scalar(
        select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id)
    )
    seq = int(last_seq or 0) + 1
    row = JobEvent(job_id=job_id, seq=seq, event_json=json.dumps(event, ensure_ascii=False))
    session.add(row)
    session.flush()
    return row


def list_events_after(session: Session, job_id: str, after_seq: int) -> list[JobEvent]:
    return list(
        session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
            .order_by(JobEvent.seq.asc())
        ).all()
    )


def claim_next_job(session: Session) -> Job | None:
    """
    Claim the next queued job.
    Uses SKIP LOCKED on Postgres; falls back to plain select+update on SQLite.
    """
    dialect = session.bind.dialect.name if session.bind is not None else "sqlite"
    stmt = (
        select(Job)
        .where(Job.status == "queued", _not_deleted())
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
    )
    if dialect == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    job = session.scalar(stmt)
    if job is None:
        return None

    job.status = "running"
    job.started_at = _utcnow()
    session.flush()
    append_event(session, job.id, {"type": "job_started", "job_id": job.id})
    return job


def set_job_status(
    session: Session,
    job_id: str,
    status: str,
    *,
    error: str | None = None,
    log_path: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if error is not None:
        values["error"] = error
    if log_path is not None:
        values["log_path"] = log_path
    if status in {"succeeded", "failed", "cancelled"}:
        values["finished_at"] = _utcnow()
    session.execute(update(Job).where(Job.id == job_id).values(**values))
    session.flush()


def get_job(session: Session, job_id: str, *, include_deleted: bool = False) -> Job | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    if not include_deleted and job.deleted_at is not None:
        return None
    return job


def soft_delete_job(session: Session, job: Job) -> Job:
    """Hide a job and cancel it if still active. Rows/events are kept."""
    if job.deleted_at is None:
        job.deleted_at = _utcnow()
    if job.status in ACTIVE:
        job.status = "cancelled"
        job.finished_at = job.finished_at or _utcnow()
        append_event(session, job.id, {"type": "job_cancelled", "job_id": job.id, "reason": "soft_deleted"})
    session.flush()
    return job


def soft_delete_jobs(session: Session, job_ids: list[str], *, user: User | None = None) -> list[str]:
    """Soft-delete many jobs. Returns ids that were soft-deleted."""
    if not job_ids:
        return []
    stmt = select(Job).where(Job.id.in_(job_ids), _not_deleted())
    if user is not None and user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    deleted: list[str] = []
    for job in session.scalars(stmt).all():
        soft_delete_job(session, job)
        deleted.append(job.id)
    return deleted


def cancel_job(session: Session, job: Job) -> Job:
    if job.deleted_at is not None:
        raise ValueError("Job is deleted")
    if job.status in {"succeeded", "failed", "cancelled"}:
        raise ValueError(f"Job already finished with status={job.status}")
    job.status = "cancelled"
    job.finished_at = _utcnow()
    session.flush()
    append_event(session, job.id, {"type": "job_cancelled", "job_id": job.id})
    return job


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "user_id": job.user_id,
        "mode": job.mode,
        "status": job.status,
        "priority": job.priority,
        "query": job.query,
        "payload": json.loads(job.payload_json or "{}"),
        "model_plan": json.loads(job.model_plan_json or "{}"),
        "error": job.error,
        "log_path": job.log_path,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "deleted_at": job.deleted_at.isoformat() if job.deleted_at else None,
    }
