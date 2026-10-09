"""Normalize prior chat turns for follow-up jobs (e.g. “write that as a docx”)."""

from __future__ import annotations

from typing import Any

MAX_TURNS = 12
MAX_CHARS_PER_TURN = 6000
MAX_TOTAL_CHARS = 24000


def normalize_conversation(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for row in raw[-MAX_TURNS:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role in {"assistant", "agent", "model"}:
            role = "assistant"
        elif role != "user":
            continue
        content = str(row.get("content") or row.get("body") or "").strip()
        if not content:
            continue
        if len(content) > MAX_CHARS_PER_TURN:
            content = content[: MAX_CHARS_PER_TURN - 1] + "…"
        out.append({"role": role, "content": content})
    # Cap total size from the end (keep most recent)
    total = 0
    kept: list[dict[str, str]] = []
    for turn in reversed(out):
        total += len(turn["content"])
        if total > MAX_TOTAL_CHARS and kept:
            break
        kept.append(turn)
    kept.reverse()
    return kept


def format_conversation_block(turns: list[dict[str, str]]) -> str:
    if not turns:
        return ""
    lines = ["## Prior conversation", "Use this context for the current request (e.g. export/summarize prior answers)."]
    for turn in turns:
        label = "User" if turn["role"] == "user" else "Assistant"
        lines.append(f"### {label}\n{turn['content']}")
    return "\n\n".join(lines)
