"""Single-call assistant path for greetings, creative writing, and simple Q&A."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import Config
from src.llm_client import LLMClient

ProgressCallback = Callable[[dict[str, Any]], None]

CHAT_SYSTEM = """You are MulteAgent Assistant — a helpful, concise local AI.

Rules:
- Match the user's intent directly (greetings → warm short reply; stories → write the story; questions → answer).
- Do NOT frame creative or casual requests as business strategy, risks, or feasibility.
- Prefer clear markdown when useful. Keep greetings to 1–3 sentences.
- Stay helpful and concrete. Do not mention being a multi-agent board unless asked."""


@dataclass
class ChatResult:
    output: str = ""
    errors: list[str] = field(default_factory=list)
    total_seconds: float = 0.0


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def run_chat(
    query: str,
    *,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> ChatResult:
    config = config or Config.from_env()
    model_plan = model_plan or {}
    model = model_plan.get("worker") or model_plan.get("planner") or config.model_fast or config.llm_model
    client = LLMClient(config)
    result = ChatResult()
    started = time.perf_counter()

    _emit(
        on_progress,
        {
            "type": "session_start",
            "mode": "chat",
            "query": query,
        },
    )
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": "Direct reply",
            "stage": "chat",
            "accent": "#c4a35a",
        },
    )

    try:
        output = client.chat(system=CHAT_SYSTEM, user=query, model=model)
        result.output = output
        elapsed = time.perf_counter() - started
        _emit(
            on_progress,
            {
                "type": "agent_done",
                "agent_id": "assistant",
                "name": "Assistant",
                "role": "Direct reply",
                "stage": "chat",
                "accent": "#c4a35a",
                "elapsed_seconds": elapsed,
                "output": output,
            },
        )
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
        _emit(
            on_progress,
            {
                "type": "agent_error",
                "agent_id": "assistant",
                "name": "Assistant",
                "error": str(exc),
            },
        )

    result.total_seconds = time.perf_counter() - started
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": result.total_seconds,
            "errors": result.errors,
            "mode": "chat",
        },
    )
    return result
