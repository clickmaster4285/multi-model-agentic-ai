"""Timestamped debate transcripts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def create_run_log(log_dir: Path, query: str, model: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = log_dir / f"debate_{stamp}.md"
    header = (
        f"# Multi-Agent Debate Log\n\n"
        f"- UTC: `{stamp}`\n"
        f"- Model: `{model}`\n\n"
        f"## User Query\n\n{query.strip()}\n\n"
        f"---\n"
    )
    path.write_text(header, encoding="utf-8")
    return path


def append_agent_section(
    path: Path,
    *,
    title: str,
    role: str,
    elapsed_seconds: float,
    content: str,
) -> None:
    block = (
        f"\n## {title}\n\n"
        f"- Role: {role}\n"
        f"- Elapsed: {elapsed_seconds:.1f}s\n\n"
        f"{content.strip()}\n\n"
        f"---\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)


def append_footer(path: Path, total_seconds: float) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## Run Complete\n\n- Total elapsed: {total_seconds:.1f}s\n")
