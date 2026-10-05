"""Background job worker (in-process or standalone)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from src.agent_store import AgentStore
from src.agentic.runtime import run_agentic
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
                set_job_status(session, job_id, "succeeded")
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
