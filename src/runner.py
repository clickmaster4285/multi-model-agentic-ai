"""Run panel agents in parallel or sequentially, then consensus."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from src.agent_store import AgentDef, AgentStore
from src.agents import Agent, AgentResult
from src.config import Config
from src.llm_client import LLMClient
from src import logger

ExecutionMode = Literal["parallel", "sequential"]
ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class RunResult:
    query: str
    mode: ExecutionMode
    panel: list[AgentResult] = field(default_factory=list)
    consensus: AgentResult | None = None
    log_path: Path | None = None
    total_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def _panel_user_message(query: str) -> str:
    return (
        "Evaluate this business query in your assigned role. "
        "Stay in character and follow your output format.\n\n"
        f"## Query\n{query.strip()}"
    )


def _consensus_user_message(query: str, panel_results: list[AgentResult]) -> str:
    blocks = []
    for result in panel_results:
        blocks.append(
            f"### {result.name} ({result.role})\n{result.output.strip()}"
        )
    joined = "\n\n".join(blocks) if blocks else "_No panel outputs._"
    return (
        "Produce an executive consensus from the panel debate below.\n\n"
        f"## Query\n{query.strip()}\n\n"
        f"## Panel Outputs\n{joined}"
    )


def _emit(callback: ProgressCallback | None, event: dict[str, Any]) -> None:
    if callback:
        callback(event)


def _run_one(
    agent_def: AgentDef,
    client: LLMClient,
    user_message: str,
    *,
    model: str | None = None,
) -> AgentResult:
    import time

    started = time.perf_counter()
    output = client.chat(
        system=agent_def.system_prompt,
        user=user_message,
        model=model,
    )
    return AgentResult(
        name=agent_def.name,
        role=agent_def.role,
        output=output,
        elapsed_seconds=time.perf_counter() - started,
    )


def run_session(
    query: str,
    *,
    mode: ExecutionMode = "sequential",
    agent_ids: list[str] | None = None,
    config: Config | None = None,
    store: AgentStore | None = None,
    on_progress: ProgressCallback | None = None,
    model_plan: dict[str, str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RunResult:
    """
    Panel agents share the same query.
    - parallel: panel agents fire concurrently (may stress 8GB VRAM)
    - sequential: panel agents run one-by-one (VRAM-safe)

    Consensus agents always run after the panel finishes.
    """
    if not query or not query.strip():
        raise ValueError("Query must be a non-empty string.")
    if mode not in ("parallel", "sequential"):
        raise ValueError("mode must be 'parallel' or 'sequential'.")

    config = config or Config.from_env()
    store = store or AgentStore()
    model_plan = model_plan or {}
    panel_model = model_plan.get("panel") or config.llm_model
    consensus_model = model_plan.get("consensus") or config.llm_model
    client = LLMClient(config)

    if not client.ping():
        raise RuntimeError(
            f"Cannot reach local LLM at {config.llm_base_url}. "
            "Start Ollama and ensure the model is pulled."
        )

    all_agents = store.load_all()
    selected = all_agents
    if agent_ids is not None:
        wanted = set(agent_ids)
        selected = [a for a in all_agents if a.id in wanted]

    panel_defs = [a for a in selected if a.enabled and a.stage == "panel"]
    consensus_defs = [a for a in selected if a.enabled and a.stage == "consensus"]

    if not panel_defs and not consensus_defs:
        raise ValueError("No enabled agents selected for this run.")

    log_path = logger.create_run_log(config.log_dir, query, consensus_model)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n- Execution mode: `{mode}`\n"
            f"- Panel model: `{panel_model}`\n"
            f"- Consensus model: `{consensus_model}`\n---\n"
        )

    result = RunResult(query=query.strip(), mode=mode, log_path=log_path)
    started = time.perf_counter()
    user_message = _panel_user_message(query)

    def _cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    _emit(
        on_progress,
        {
            "type": "session_start",
            "mode": mode,
            "query": result.query,
            "panel_ids": [a.id for a in panel_defs],
            "consensus_ids": [a.id for a in consensus_defs],
            "model": consensus_model,
            "model_plan": model_plan,
        },
    )

    # --- Panel stage ---
    if panel_defs:
        for agent_def in panel_defs:
            _emit(
                on_progress,
                {
                    "type": "agent_start",
                    "agent_id": agent_def.id,
                    "name": agent_def.name,
                    "role": agent_def.role,
                    "stage": "panel",
                    "accent": agent_def.accent,
                },
            )

        if _cancelled():
            result.errors.append("Cancelled before panel stage.")
            result.total_seconds = time.perf_counter() - started
            return result

        if mode == "parallel" and len(panel_defs) > 1:
            # Concurrent HTTP calls; LLM semaphore serializes GPU use.
            max_workers = min(len(panel_defs), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        _run_one, agent_def, client, user_message, model=panel_model
                    ): agent_def
                    for agent_def in panel_defs
                }
                for future in as_completed(futures):
                    agent_def = futures[future]
                    try:
                        agent_result = future.result()
                        result.panel.append(agent_result)
                        logger.append_agent_section(
                            log_path,
                            title=f"{agent_result.name}: {agent_result.role}",
                            role=agent_result.role,
                            elapsed_seconds=agent_result.elapsed_seconds,
                            content=agent_result.output,
                        )
                        _emit(
                            on_progress,
                            {
                                "type": "agent_done",
                                "agent_id": agent_def.id,
                                "name": agent_result.name,
                                "role": agent_result.role,
                                "stage": "panel",
                                "accent": agent_def.accent,
                                "elapsed_seconds": agent_result.elapsed_seconds,
                                "output": agent_result.output,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        message = f"{agent_def.name}: {exc}"
                        result.errors.append(message)
                        _emit(
                            on_progress,
                            {
                                "type": "agent_error",
                                "agent_id": agent_def.id,
                                "name": agent_def.name,
                                "stage": "panel",
                                "error": str(exc),
                            },
                        )
            # Stable display order matching sort_order
            order = {a.name: i for i, a in enumerate(panel_defs)}
            result.panel.sort(key=lambda r: order.get(r.name, 999))
        else:
            for agent_def in panel_defs:
                if _cancelled():
                    result.errors.append("Cancelled during panel stage.")
                    break
                try:
                    agent_result = _run_one(
                        agent_def, client, user_message, model=panel_model
                    )
                    result.panel.append(agent_result)
                    logger.append_agent_section(
                        log_path,
                        title=f"{agent_result.name}: {agent_result.role}",
                        role=agent_result.role,
                        elapsed_seconds=agent_result.elapsed_seconds,
                        content=agent_result.output,
                    )
                    _emit(
                        on_progress,
                        {
                            "type": "agent_done",
                            "agent_id": agent_def.id,
                            "name": agent_result.name,
                            "role": agent_result.role,
                            "stage": "panel",
                            "accent": agent_def.accent,
                            "elapsed_seconds": agent_result.elapsed_seconds,
                            "output": agent_result.output,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    message = f"{agent_def.name}: {exc}"
                    result.errors.append(message)
                    _emit(
                        on_progress,
                        {
                            "type": "agent_error",
                            "agent_id": agent_def.id,
                            "name": agent_def.name,
                            "stage": "panel",
                            "error": str(exc),
                        },
                    )

    # --- Consensus stage (always after panel) ---
    if consensus_defs and result.panel:
        consensus_message = _consensus_user_message(query, result.panel)
        # Use first enabled consensus agent (support multiple later if needed)
        consensus_def = consensus_defs[0]
        _emit(
            on_progress,
            {
                "type": "agent_start",
                "agent_id": consensus_def.id,
                "name": consensus_def.name,
                "role": consensus_def.role,
                "stage": "consensus",
                "accent": consensus_def.accent,
            },
        )
        if _cancelled():
            result.errors.append("Cancelled before consensus.")
            result.total_seconds = time.perf_counter() - started
            logger.append_footer(log_path, result.total_seconds)
            return result
        try:
            consensus_result = _run_one(
                consensus_def, client, consensus_message, model=consensus_model
            )
            result.consensus = consensus_result
            logger.append_agent_section(
                log_path,
                title=f"{consensus_result.name}: {consensus_result.role}",
                role=consensus_result.role,
                elapsed_seconds=consensus_result.elapsed_seconds,
                content=consensus_result.output,
            )
            _emit(
                on_progress,
                {
                    "type": "agent_done",
                    "agent_id": consensus_def.id,
                    "name": consensus_result.name,
                    "role": consensus_result.role,
                    "stage": "consensus",
                    "accent": consensus_def.accent,
                    "elapsed_seconds": consensus_result.elapsed_seconds,
                    "output": consensus_result.output,
                },
            )
        except Exception as exc:  # noqa: BLE001
            message = f"{consensus_def.name}: {exc}"
            result.errors.append(message)
            _emit(
                on_progress,
                {
                    "type": "agent_error",
                    "agent_id": consensus_def.id,
                    "name": consensus_def.name,
                    "stage": "consensus",
                    "error": str(exc),
                },
            )
    elif consensus_defs and not result.panel:
        result.errors.append("Consensus skipped: no successful panel outputs.")

    result.total_seconds = time.perf_counter() - started
    logger.append_footer(log_path, result.total_seconds)
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": result.total_seconds,
            "log_path": str(log_path),
            "errors": result.errors,
        },
    )
    return result
