"""Planner → worker tool loop → critic agentic runtime."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agentic import prompts
from src.agentic.tools import ToolError, run_tool
from src.config import Config
from src.llm_client import LLMClient

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class AgenticResult:
    plan: list[dict[str, Any]] = field(default_factory=list)
    step_outputs: list[dict[str, Any]] = field(default_factory=list)
    critic: str = ""
    final_summary: str = ""
    errors: list[str] = field(default_factory=list)
    total_seconds: float = 0.0


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def _parse_plan(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return [{"id": 1, "title": "Execute goal", "action": text[:500]}]
        data = json.loads(match.group(0))
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not steps:
        return [{"id": 1, "title": "Execute goal", "action": "Complete the user goal"}]
    cleaned = []
    for i, step in enumerate(steps[:6], start=1):
        cleaned.append(
            {
                "id": int(step.get("id") or i),
                "title": str(step.get("title") or f"Step {i}"),
                "action": str(step.get("action") or ""),
            }
        )
    return cleaned


def _parse_worker(text: str) -> tuple[str, str | None, dict[str, Any] | None]:
    stripped = text.strip()
    if stripped.upper().startswith("TOOL "):
        rest = stripped[5:].strip()
        parts = rest.split(" ", 1)
        name = parts[0].strip()
        args_raw = parts[1].strip() if len(parts) > 1 else "{}"
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {"raw": args_raw}
        return "tool", name, args if isinstance(args, dict) else {"value": args}
    if stripped.upper().startswith("FINAL "):
        return "final", None, {"content": stripped[6:].strip()}
    return "final", None, {"content": stripped}


def run_agentic(
    goal: str,
    *,
    job_id: str,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    max_steps: int = 6,
    max_tool_rounds: int = 3,
    on_progress: ProgressCallback | None = None,
) -> AgenticResult:
    config = config or Config.from_env()
    model_plan = model_plan or {}
    client = LLMClient(config)
    result = AgenticResult()
    started = time.perf_counter()

    _emit(on_progress, {"type": "agentic_start", "goal": goal, "job_id": job_id})

    planner_model = model_plan.get("planner") or config.model_fast or config.llm_model
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "planner",
            "name": "Planner",
            "role": "Plan",
            "stage": "agentic",
            "accent": "#6b8cae",
        },
    )
    plan_raw = client.chat(
        system=prompts.PLANNER_SYSTEM,
        user=f"## Goal\n{goal}",
        model=planner_model,
    )
    result.plan = _parse_plan(plan_raw)
    _emit(
        on_progress,
        {
            "type": "agent_done",
            "agent_id": "planner",
            "name": "Planner",
            "role": "Plan",
            "stage": "agentic",
            "accent": "#6b8cae",
            "elapsed_seconds": 0,
            "output": json.dumps(result.plan, indent=2),
        },
    )

    worker_model = model_plan.get("worker") or config.model_strong or config.llm_model
    for step in result.plan[:max_steps]:
        step_id = step["id"]
        _emit(
            on_progress,
            {
                "type": "agent_start",
                "agent_id": f"worker-{step_id}",
                "name": f"Worker step {step_id}",
                "role": step["title"],
                "stage": "agentic",
                "accent": "#3aa89a",
            },
        )
        observations: list[str] = []
        final_text = ""
        for _round in range(max_tool_rounds):
            user_msg = (
                f"## Goal\n{goal}\n\n"
                f"## Current step\n{json.dumps(step)}\n\n"
                f"## Observations so far\n{chr(10).join(observations) or 'None'}\n"
            )
            raw = client.chat(system=prompts.WORKER_SYSTEM, user=user_msg, model=worker_model)
            kind, tool_name, payload = _parse_worker(raw)
            if kind == "tool" and tool_name:
                _emit(
                    on_progress,
                    {
                        "type": "tool_call",
                        "agent_id": f"worker-{step_id}",
                        "tool": tool_name,
                        "args": payload or {},
                    },
                )
                try:
                    obs = run_tool(tool_name, payload or {}, job_id=job_id, config=config)
                except (ToolError, Exception) as exc:  # noqa: BLE001
                    obs = f"TOOL_ERROR: {exc}"
                observations.append(f"{tool_name}: {obs}")
                _emit(
                    on_progress,
                    {
                        "type": "tool_result",
                        "agent_id": f"worker-{step_id}",
                        "tool": tool_name,
                        "result": obs[:4000],
                    },
                )
                continue
            final_text = (payload or {}).get("content", raw)
            break

        if not final_text:
            final_text = observations[-1] if observations else "No output."
        result.step_outputs.append({"step": step, "output": final_text, "observations": observations})
        _emit(
            on_progress,
            {
                "type": "agent_done",
                "agent_id": f"worker-{step_id}",
                "name": f"Worker step {step_id}",
                "role": step["title"],
                "stage": "agentic",
                "accent": "#3aa89a",
                "elapsed_seconds": 0,
                "output": final_text,
            },
        )

    critic_model = model_plan.get("critic") or config.model_strong or config.llm_model
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "critic",
            "name": "Critic",
            "role": "Review",
            "stage": "agentic",
            "accent": "#c44b3c",
        },
    )
    critic_user = (
        f"## Goal\n{goal}\n\n"
        f"## Plan\n{json.dumps(result.plan, indent=2)}\n\n"
        f"## Step results\n{json.dumps(result.step_outputs, indent=2)[:14000]}"
    )
    result.critic = client.chat(system=prompts.CRITIC_SYSTEM, user=critic_user, model=critic_model)
    _emit(
        on_progress,
        {
            "type": "agent_done",
            "agent_id": "critic",
            "name": "Critic",
            "role": "Review",
            "stage": "agentic",
            "accent": "#c44b3c",
            "elapsed_seconds": 0,
            "output": result.critic,
        },
    )

    # Compact summary from step finals
    parts = [f"### {s['step']['title']}\n{s['output']}" for s in result.step_outputs]
    result.final_summary = "\n\n".join(parts)
    result.total_seconds = time.perf_counter() - started
    _emit(
        on_progress,
        {
            "type": "agentic_done",
            "total_seconds": result.total_seconds,
            "summary": result.final_summary[:2000],
        },
    )
    return result
