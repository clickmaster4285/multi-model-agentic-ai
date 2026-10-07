"""Background job worker (in-process or standalone)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from src.agent_store import AgentStore
from src.agentic.runtime import run_agentic
from src.chat_runner import run_chat
from src.image_gen import run_image_gen
from src.attachments import load_b64_images
from src.config import Config
from src.db import session_scope
from src.jobs import append_event, claim_next_job, get_job, set_job_status
from src.runner import run_session

_worker_thread: threading.Thread | None = None
_stop = threading.Event()


def _is_cancelled(job_id: str) -> bool:
    with session_scope() as session:
        job = get_job(session, job_id)
        return bool(job and job.status == "cancelled")


def process_job(job_id: str, config: Config | None = None) -> None:
    config = config or Config.from_env()
    with session_scope(config) as session:
        job = get_job(session, job_id)
        if job is None:
            return
        mode = job.mode
        query = job.query
        payload = json.loads(job.payload_json or "{}")
        model_plan = json.loads(job.model_plan_json or "{}")

    def on_progress(event: dict[str, Any]) -> None:
        if _is_cancelled(job_id):
            return
        with session_scope(config) as session:
            current = get_job(session, job_id)
            if current is None or current.status == "cancelled":
                return
            append_event(session, job_id, event)

    try:
        if _is_cancelled(job_id):
            return

        route = payload.get("route") if isinstance(payload.get("route"), dict) else None
        if route:
            on_progress({"type": "route_decided", **route})

        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
        has_images = bool(payload.get("has_images") or attachments)
        images = load_b64_images(config, attachments) if attachments else []

        # Image + query always goes through vision chat, even if a heavier mode leaked in.
        if has_images or images:
            result = run_chat(
                query,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
                images=images,
            )
            with session_scope(config) as session:
                if get_job(session, job_id) and get_job(session, job_id).status == "cancelled":
                    return
                status = "failed" if result.errors and not result.output else "succeeded"
                set_job_status(
                    session,
                    job_id,
                    status,
                    error="; ".join(result.errors) if result.errors else None,
                )
            return

        if (route or {}).get("intent") == "image_gen":
            _output, saved, errors, _elapsed = run_image_gen(
                query,
                job_id=job_id,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
            )
            with session_scope(config) as session:
                current = get_job(session, job_id)
                if current is None or current.status == "cancelled":
                    return
                payload_now = json.loads(current.payload_json or "{}")
                if saved:
                    existing = payload_now.get("attachments") if isinstance(payload_now.get("attachments"), list) else []
                    payload_now["attachments"] = existing + saved
                    payload_now["generated"] = saved
                    current.payload_json = json.dumps(payload_now)
                status = "failed" if errors and not saved else "succeeded"
                set_job_status(
                    session,
                    job_id,
                    status,
                    error="; ".join(errors) if errors else None,
                )
            return

        if mode == "chat":
            result = run_chat(
                query,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
                images=[],
            )
            with session_scope(config) as session:
                if get_job(session, job_id) and get_job(session, job_id).status == "cancelled":
                    return
                status = "failed" if result.errors and not result.output else "succeeded"
                set_job_status(
                    session,
                    job_id,
                    status,
                    error="; ".join(result.errors) if result.errors else None,
                )
            return

        if mode == "agentic":
            result = run_agentic(
                query,
                job_id=job_id,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
            )
            with session_scope(config) as session:
                if get_job(session, job_id) and get_job(session, job_id).status == "cancelled":
                    return
                # Emit completion event before terminal status so SSE clients drain it.
                append_event(
                    session,
                    job_id,
                    {
                        "type": "session_done",
                        "total_seconds": result.total_seconds,
                        "log_path": "",
                        "errors": result.errors,
                    },
                )
                set_job_status(session, job_id, "succeeded")
            return

        if mode == "mixed":
            agentic = run_agentic(
                query,
                job_id=job_id,
                config=config,
                model_plan=model_plan,
                on_progress=on_progress,
            )
            debate_query = (
                f"{query}\n\n## Agentic research summary\n{agentic.final_summary[:6000]}\n\n"
                f"## Critic\n{agentic.critic}"
            )
        else:
            debate_query = query

        if _is_cancelled(job_id):
            return

        exec_mode = payload.get("execution_mode") or payload.get("mode") or "sequential"
        if exec_mode not in {"parallel", "sequential"}:
            exec_mode = "sequential"

        result = run_session(
            debate_query,
            mode=exec_mode,
            agent_ids=payload.get("agent_ids"),
            config=config,
            store=AgentStore(),
            on_progress=on_progress,
            model_plan=model_plan,
            should_cancel=lambda: _is_cancelled(job_id),
        )

        with session_scope(config) as session:
            current = get_job(session, job_id)
            if current is None:
                return
            if current.status == "cancelled":
                return
            # session_done was already appended via on_progress inside run_session.
            status = "failed" if result.errors and result.consensus is None else "succeeded"
            set_job_status(
                session,
                job_id,
                status,
                error="; ".join(result.errors) if result.errors else None,
                log_path=str(result.log_path) if result.log_path else None,
            )
    except Exception as exc:  # noqa: BLE001
        with session_scope(config) as session:
            current = get_job(session, job_id)
            if current and current.status != "cancelled":
                set_job_status(session, job_id, "failed", error=str(exc))
                append_event(session, job_id, {"type": "fatal", "error": str(exc)})


def worker_loop(config: Config | None = None, poll_seconds: float = 1.0) -> None:
    config = config or Config.from_env()
    while not _stop.is_set():
        job_id = None
        try:
            with session_scope(config) as session:
                job = claim_next_job(session)
                if job is not None:
                    job_id = job.id
        except Exception:
            job_id = None

        if job_id:
            process_job(job_id, config)
        else:
            _stop.wait(poll_seconds)


def start_inprocess_worker(config: Config | None = None) -> None:
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop.clear()
    _worker_thread = threading.Thread(
        target=worker_loop,
        kwargs={"config": config or Config.from_env()},
        name="multeagent-worker",
        daemon=True,
    )
    _worker_thread.start()


def stop_inprocess_worker() -> None:
    _stop.set()
