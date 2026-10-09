"""FastAPI gateway: auth, agents, models, jobs, legacy debate."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent_store import AgentStore
from src.attachments import image_path
from src.artifacts import resolve_job_file
from src.auth import (
    bootstrap_admin,
    create_access_token,
    get_current_user,
    get_db,
    require_admin,
    verify_password,
)
from src.config import Config
from src.db import init_db
from src.jobs import (
    cancel_job,
    create_job,
    estimate_wait_seconds,
    get_job,
    job_to_dict,
    list_events_after,
    queue_depth,
    soft_delete_job,
    soft_delete_jobs,
)
from src.llm_client import LLMClient
from src.model_registry import list_models, sync_models_from_ollama
from src.models_db import Job, ModelRecord, User
from src.worker import start_inprocess_worker

STATIC_DIR = Path(__file__).resolve().parent / "static"
store = AgentStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from src.db import session_scope

    config = Config.from_env()
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.artifacts_dir.mkdir(parents=True, exist_ok=True)
    init_db(config)
    bootstrap_admin(config)
    with session_scope(config) as session:
        sync_models_from_ollama(session, config, probe_details=True)
    start_inprocess_worker(config)
    if config.image_warm_on_start:
        from src.sdxl_pipeline import warm_pipeline_async

        warm_pipeline_async(config)
    yield


app = FastAPI(title="MulteAgent API", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginPayload(BaseModel):
    username: str
    password: str


class AgentPayload(BaseModel):
    name: str
    role: str = ""
    system_prompt: str
    stage: Literal["panel", "consensus"] = "panel"
    enabled: bool = True
    sort_order: int | None = None
    accent: str = "#6b8cae"


class AgentUpdatePayload(BaseModel):
    name: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    stage: Literal["panel", "consensus"] | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    accent: str | None = None


class SoftDeleteJobsPayload(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class ImageAttachmentIn(BaseModel):
    filename: str = "image.png"
    mime: str = "image/png"
    data: str


class ConversationTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class JobCreatePayload(BaseModel):
    query: str = ""
    mode: Literal["auto", "chat", "debate", "agentic", "mixed"] = "auto"
    execution_mode: Literal["parallel", "sequential"] = "sequential"
    agent_ids: list[str] | None = None
    model: str | None = None
    allow_overflow: bool = True
    priority: int = 100
    images: list[ImageAttachmentIn] = Field(default_factory=list)
    # Prior turns in this chat (so “write that as docx” sees previous answers)
    conversation: list[ConversationTurnIn] = Field(default_factory=list)
    # Explicit composer actions: describe | generate | edit | inpaint | doc_gen
    force_intent: str | None = None
    image_profile: Literal["fast", "quality", "balanced"] | None = None
    image_strength: float | None = None


class ModelUpdatePayload(BaseModel):
    enabled: bool | None = None
    role: Literal["fast", "strong", "cloud", "general"] | None = None
    vram_class: Literal["small", "medium", "large"] | None = None
    max_concurrency: int | None = None


class DebateRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["parallel", "sequential"] = "sequential"
    agent_ids: list[str] | None = None


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, Any]:
    from src.sdxl_pipeline import pipeline_status

    config = Config.from_env()
    client = LLMClient(config)
    return {
        "ok": True,
        "llm_reachable": client.ping(),
        "model": config.llm_model,
        "base_url": config.llm_base_url,
        "queue_depth": queue_depth(db),
        "estimated_wait_seconds": estimate_wait_seconds(db, config),
        "llm_slots": config.llm_slots,
        "image_slots": config.image_slots,
        "image_profile": config.image_profile,
        "image_pipeline": pipeline_status(),
        "sso_enabled": config.sso_enabled,
        "auth_required": True,
    }


@app.get("/api/auth/sso/status")
def sso_status() -> dict[str, Any]:
    config = Config.from_env()
    return {
        "enabled": config.sso_enabled,
        "message": (
            "SSO hook ready — set SSO_ENABLED=true and wire your IdP callback in a future deploy."
            if config.sso_enabled
            else "SSO disabled. Using local JWT accounts."
        ),
    }


@app.post("/api/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "username": user.username, "role": user.role},
    }


@app.post("/api/auth/register")
def register_disabled() -> dict[str, Any]:
    """Multi-user registration is deferred — use default admin for testing."""
    raise HTTPException(
        status_code=403,
        detail="User registration is disabled for now. Sign in as admin (default: admin / admin123).",
    )


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "daily_job_quota": user.daily_job_quota,
    }


@app.get("/api/agents")
def list_agents(_user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    return [a.to_dict() for a in store.load_all()]


@app.post("/api/agents")
def create_agent(
    payload: AgentPayload, _user: User = Depends(get_current_user)
) -> dict[str, Any]:
    try:
        agent = store.create(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return agent.to_dict()


@app.put("/api/agents/{agent_id}")
def update_agent(
    agent_id: str,
    payload: AgentUpdatePayload,
    _user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        agent = store.update(agent_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return agent.to_dict()


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: str, _user: User = Depends(get_current_user)) -> dict[str, str]:
    try:
        store.delete(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted", "id": agent_id}


@app.post("/api/agents/reset")
def reset_agents(_user: User = Depends(require_admin)) -> list[dict[str, Any]]:
    return [a.to_dict() for a in store.reset_defaults()]


@app.get("/api/models")
def get_models(
    refresh: bool = False,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    sync_models_from_ollama(db, Config.from_env(), probe_details=refresh)
    db.commit()
    return list_models(db)


@app.patch("/api/models/{model_name:path}")
def patch_model(
    model_name: str,
    payload: ModelUpdatePayload,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    row = db.scalar(select(ModelRecord).where(ModelRecord.name == model_name))
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    data = payload.model_dump(exclude_none=True)
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return {
        "name": row.name,
        "backend": row.backend,
        "vram_class": row.vram_class,
        "role": row.role,
        "max_concurrency": row.max_concurrency,
        "enabled": row.enabled,
        "vision": bool(getattr(row, "vision", False)),
        "image_gen": bool(getattr(row, "image_gen", False)),
        "available": bool(getattr(row, "available", True)),
    }


@app.post("/api/jobs")
def create_job_endpoint(
    payload: JobCreatePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    config = Config.from_env()
    try:
        job = create_job(
            db,
            user=user,
            query=payload.query,
            mode=payload.mode,
            payload={
                "execution_mode": payload.execution_mode,
                "agent_ids": payload.agent_ids,
                "model": payload.model,
                "allow_overflow": payload.allow_overflow,
                "images": [img.model_dump() for img in payload.images],
                "conversation": [t.model_dump() for t in payload.conversation],
                "force_intent": payload.force_intent,
                "image_profile": payload.image_profile,
                "image_strength": payload.image_strength,
            },
            config=config,
            priority=payload.priority if user.role == "admin" else 100,
        )
        db.commit()
        db.refresh(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_to_dict(job)


@app.get("/api/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = (
        select(Job)
        .where(Job.deleted_at.is_(None))
        .order_by(Job.created_at.desc())
        .limit(min(limit, 100))
    )
    if user.role != "admin":
        stmt = stmt.where(Job.user_id == user.id)
    return [job_to_dict(j) for j in db.scalars(stmt).all()]


@app.post("/api/jobs/soft-delete")
def soft_delete_jobs_endpoint(
    payload: SoftDeleteJobsPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Soft-delete one or more jobs (conversation turns). Data is retained."""
    ids = [jid.strip() for jid in payload.job_ids if jid and jid.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="job_ids required")
    deleted = soft_delete_jobs(db, ids, user=user)
    db.commit()
    return {"status": "soft_deleted", "deleted_ids": deleted, "count": len(deleted)}


@app.delete("/api/jobs/{job_id}")
def soft_delete_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    soft_delete_job(db, job)
    db.commit()
    return {"status": "soft_deleted", "id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return job_to_dict(job)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_endpoint(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        job = cancel_job(db, job)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job_to_dict(job)


@app.get("/api/jobs/{job_id}/history")
def job_events_history(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return all stored events for a job (for chat history replay)."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = list_events_after(db, job_id, 0)
    events = []
    for row in rows:
        try:
            events.append(json.loads(row.event_json))
        except json.JSONDecodeError:
            events.append({"type": "raw", "body": row.event_json})
    return {"job": job_to_dict(job), "events": events}


@app.get("/api/jobs/{job_id}/attachments/{filename}")
def job_attachment(
    job_id: str,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Download an image or generated document for this job."""
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    config = Config.from_env()
    payload = json.loads(job.payload_json or "{}")
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    match = next((a for a in attachments if a.get("filename") == filename), None)
    mime = "application/octet-stream"
    try:
        if match and match.get("relpath"):
            # Images use attachments.image_path; docs may use files/ via resolve_job_file.
            rel = str(match["relpath"])
            mime = str(match.get("mime") or mime)
            try:
                path = image_path(config, rel)
            except (ValueError, FileNotFoundError):
                path = resolve_job_file(config, job_id, filename)
        else:
            path = resolve_job_file(config, job_id, filename)
            from src.artifacts import mime_for

            mime = mime_for(filename)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail="Attachment missing") from exc
    return FileResponse(path, media_type=mime, filename=filename)


@app.get("/api/jobs/{job_id}/events")
async def job_events_stream(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    job = get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "admin" and job.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    async def event_generator():
        from src.db import session_scope

        last_seq = 0
        terminal = {"succeeded", "failed", "cancelled"}
        idle_rounds = 0
        while True:
            payloads: list[tuple[int, str]] = []
            status = "queued"
            with session_scope() as session:
                current = get_job(session, job_id)
                if current is None:
                    yield f"data: {json.dumps({'type': 'fatal', 'error': 'Job missing'})}\n\n"
                    break
                status = current.status
                rows = list_events_after(session, job_id, last_seq)
                # Copy fields inside the session to avoid DetachedInstanceError.
                payloads = [(row.seq, row.event_json) for row in rows]
            for seq, event_json in payloads:
                last_seq = seq
                yield f"data: {event_json}\n\n"
            if status in terminal:
                # Keep draining until events stop arriving (avoid cutting off last msgs).
                idle_rounds = idle_rounds + 1 if not payloads else 0
                if idle_rounds >= 5:
                    break
            else:
                idle_rounds = 0
            # Wake early when append_event notifies; fall back to short poll.
            from src.job_events import wait_job

            await asyncio.to_thread(wait_job, job_id, 0.35)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/debate")
def debate_compat(
    payload: DebateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Backward-compatible: enqueue a debate job instead of inline streaming."""
    try:
        job = create_job(
            db,
            user=user,
            query=payload.query,
            mode="debate",
            payload={
                "execution_mode": payload.mode,
                "agent_ids": payload.agent_ids,
                "allow_overflow": True,
            },
            config=Config.from_env(),
        )
        db.commit()
        db.refresh(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_to_dict(job)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    config = Config.from_env()
    uvicorn.run("web.server:app", host=config.api_host, port=config.api_port, reload=False)


if __name__ == "__main__":
    main()
