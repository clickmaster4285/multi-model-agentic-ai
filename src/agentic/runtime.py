"""Planner → worker tool loop → critic agentic runtime."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from src.agentic import prompts
from src.agentic.tools import ToolError, run_tool
from src.config import Config
from src.conversation import format_conversation_block, normalize_conversation
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


def _close_truncated_json(raw: str) -> str:
    """Best-effort close of a JSON object cut off mid-string (max_tokens truncation)."""
    s = raw.strip()
    in_str = False
    escaped = False
    stack: list[str] = []
    for ch in s:
        if escaped:
            escaped = False
            continue
        if in_str and ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            elif ch == "}" and stack and stack[-1] == "]":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "}":
                stack.pop()
    out = s
    if in_str:
        # Drop a trailing partial key (e.g. `,"headin`) before closing the string.
        cut = out.rstrip()
        partial_key = re.search(r",\s*\"[^\"]*$", cut)
        if partial_key:
            out = cut[: partial_key.start()]
        else:
            out = cut + '"'
    out = out.rstrip()
    while out.endswith((",", ":")):
        out = out[:-1].rstrip()
    while stack:
        out += stack.pop()
    return out


def _try_parse_tool_args(args_raw: str) -> dict[str, Any] | None:
    text = args_raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*", text, flags=re.DOTALL)
    candidate = match.group(0) if match else text
    try:
        data = json.loads(_close_truncated_json(candidate))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return None
    return None


_XML_TOOL_RE = re.compile(
    r"<([a-zA-Z_][\w-]*)>\s*(.*?)\s*</\1>\s*$",
    re.DOTALL,
)


def _args_from_xml_like(tool_name: str, body: str) -> dict[str, Any]:
    """Map <write_docx><file_name>…</file_name><content>…</content></write_docx> style output."""
    fields: dict[str, str] = {}
    for tag in ("file_name", "filename", "file", "title", "subtitle", "content", "body", "template"):
        m = re.search(
            rf"<{tag}>\s*(.*?)\s*</{tag}>",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if m:
            fields[tag] = m.group(1).strip()
    filename = fields.get("file_name") or fields.get("filename") or fields.get("file") or ""
    if not filename and tool_name.startswith("write_"):
        ext = tool_name.removeprefix("write_")
        if ext in {"docx", "pdf", "pptx", "xlsx", "html"}:
            filename = f"document.{ext}"
    content = fields.get("content") or fields.get("body") or body.strip()
    title = fields.get("title") or Path(filename).stem if filename else "Document"
    args: dict[str, Any] = {"filename": filename or "document.txt", "title": title}
    if fields.get("subtitle"):
        args["subtitle"] = fields["subtitle"]
    if fields.get("template"):
        args["template"] = fields["template"]
    if content:
        args["content"] = content
    return args


def _parse_worker(text: str) -> tuple[str, str | None, dict[str, Any] | None]:
    stripped = text.strip()
    upper = stripped.upper()
    if upper.startswith("TOOL "):
        rest = stripped[5:].strip()
        parts = rest.split(" ", 1)
        name = parts[0].strip()
        args_raw = parts[1].strip() if len(parts) > 1 else "{}"
        # Separate trailing plain-text content after the JSON object.
        content_tail = ""
        brace = args_raw.find("{")
        if brace >= 0:
            args_body = args_raw[brace:]
            args = _try_parse_tool_args(args_body)
            if args is not None:
                # Trailing text after closing } (e.g. markdown body the model appended)
                end = args_body.rfind("}")
                tail = args_body[end + 1 :].strip() if end >= 0 else ""
                if tail and not args.get("content"):
                    content_tail = tail
                if content_tail:
                    args["content"] = f"{args.get('content', '')}\n{content_tail}".strip()
                return "tool", name, args
            # Salvage known fields from broken/truncated JSON.
            salvaged = _salvage_broken_args(name, args_body)
            if salvaged:
                return "tool", name, salvaged
            return "tool", name, {"raw": args_raw}
        return "tool", name, {"raw": args_raw}
    if upper.startswith("FINAL "):
        return "final", None, {"content": stripped[6:].strip()}
    # Invented XML-style tool call (common with smaller local models).
    xml = _XML_TOOL_RE.search(stripped)
    if xml:
        tag = xml.group(1).strip()
        body = xml.group(2)
        known = {
            "write_docx",
            "write_pdf",
            "write_pptx",
            "write_xlsx",
            "write_html",
            "write_artifact",
            "package_zip",
        }
        if tag in known or tag.startswith("write_"):
            return "tool", tag, _args_from_xml_like(tag, body)
    return "final", None, {"content": stripped}


def _salvage_broken_args(tool_name: str, raw: str) -> dict[str, Any] | None:
    """Pull filename/title/content out of truncated or half-valid tool JSON."""
    text = raw.strip()
    if not text:
        return None
    out: dict[str, Any] = {}
    for key in ("filename", "title", "template", "subtitle"):
        m = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text)
        if m:
            try:
                out[key] = json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                out[key] = m.group(1)
    bodies: list[str] = []
    for m in re.finditer(r'"(?:body|content)"\s*:\s*"((?:\\.|[^"\\])*)"', text):
        chunk = m.group(1)
        try:
            chunk = json.loads(f'"{chunk}"')
        except json.JSONDecodeError:
            chunk = chunk.replace("\\n", "\n").replace('\\"', '"')
        chunk = chunk.strip()
        if chunk:
            bodies.append(chunk)
    # Whole raw blob as last-resort content when bodies are unusable.
    if bodies:
        out["content"] = "\n\n".join(bodies)
    elif len(text) > 80 and not text.lstrip().startswith("{"):
        out["content"] = text
    if not out.get("filename") and tool_name.startswith("write_"):
        ext = tool_name.removeprefix("write_")
        if ext in {"docx", "pdf", "pptx", "xlsx", "html"}:
            out["filename"] = f"document.{ext}"
    if not out.get("title") and out.get("filename"):
        out["title"] = Path(str(out["filename"])).stem
    meaningful = bool(out.get("content") or out.get("filename") or out.get("title"))
    if not meaningful:
        return None
    # Avoid dropping the only clue: keep raw for write_* content fallback.
    if tool_name.startswith("write_") and not out.get("content"):
        out["raw"] = text[:50_000]
    return out


def run_agentic(
    goal: str,
    *,
    job_id: str,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    conversation: list[dict[str, str]] | None = None,
    max_steps: int = 6,
    max_tool_rounds: int = 3,
    on_progress: ProgressCallback | None = None,
) -> AgenticResult:
    config = config or Config.from_env()
    model_plan = model_plan or {}
    client = LLMClient(config)
    result = AgenticResult()
    started = time.perf_counter()
    prior = format_conversation_block(normalize_conversation(conversation))
    goal_block = f"{prior}\n\n## Goal\n{goal}" if prior else f"## Goal\n{goal}"

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
        user=goal_block,
        model=planner_model,
    )
    result.plan = _parse_plan(plan_raw)
    plan_md = "\n".join(
        f"{i}. **{step.get('title') or f'Step {i}'}** — {step.get('action') or ''}".strip(" —")
        for i, step in enumerate(result.plan, start=1)
    ) or "_No steps planned._"
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
            "output": plan_md,
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
                f"{goal_block}\n\n"
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
                # Surface downloadable files created by write_* / package_zip tools.
                try:
                    parsed = json.loads(obs)
                    event = parsed.get("event") if isinstance(parsed, dict) else None
                    if isinstance(event, dict) and event.get("type") == "artifact_ready":
                        _emit(on_progress, event)
                except (json.JSONDecodeError, TypeError):
                    pass
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
        f"{goal_block}\n\n"
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
