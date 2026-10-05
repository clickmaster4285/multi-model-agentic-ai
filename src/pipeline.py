"""Backward-compatible debate entrypoint wrapping the new runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.agents import AgentResult
from src.config import Config
from src.runner import run_session


@dataclass
class DebateResult:
    query: str
    optimist: AgentResult
    cynic: AgentResult
    consensus: AgentResult
    log_path: Path
    total_seconds: float


def run_debate(
    query: str,
    config: Config | None = None,
    *,
    mode: str = "sequential",
) -> DebateResult:
    """
    Classic Optimist / Cynic / Consensus debate.

    Panel agents share the query (independent). Consensus synthesizes after.
    Use mode='parallel' to run panel agents concurrently.
    """
    result = run_session(
        query,
        mode=mode if mode in ("parallel", "sequential") else "sequential",
        agent_ids=["optimist", "cynic", "consensus"],
        config=config,
    )

    by_name = {item.name.lower(): item for item in result.panel}
    optimist = by_name.get("optimist")
    cynic = by_name.get("cynic")
    if optimist is None or cynic is None or result.consensus is None:
        detail = "; ".join(result.errors) or "missing agent outputs"
        raise RuntimeError(f"Debate incomplete: {detail}")

    return DebateResult(
        query=result.query,
        optimist=optimist,
        cynic=cynic,
        consensus=result.consensus,
        log_path=result.log_path or Path("logs"),
        total_seconds=result.total_seconds,
    )
