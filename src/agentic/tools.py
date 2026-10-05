"""Allowlisted tools for agentic workers (no arbitrary shell)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from src.config import Config, ROOT
from src.llm_client import LLMClient


class ToolError(RuntimeError):
    pass


def _allowlisted(url: str, config: Config) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    allowed = {h.strip().lower() for h in config.http_allowlist.split(",") if h.strip()}
    return host in allowed or any(host.endswith(f".{h}") for h in allowed)


def run_tool(
    name: str,
    args: dict[str, Any],
    *,
    job_id: str,
    config: Config,
) -> str:
    if name == "read_repo_file":
        rel = str(args.get("path") or "").replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise ToolError("Path traversal is not allowed.")
        path = (ROOT / rel).resolve()
        if not str(path).startswith(str(ROOT.resolve())):
            raise ToolError("Path outside workspace.")
        if not path.is_file():
            raise ToolError(f"File not found: {rel}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:12000]

    if name == "write_artifact":
        filename = Path(str(args.get("filename") or "artifact.md")).name
        content = str(args.get("content") or "")
        out_dir = config.artifacts_dir / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_text(content, encoding="utf-8")
        return f"Wrote artifact: {path}"

    if name == "http_get":
        url = str(args.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ToolError("Only http/https URLs are allowed.")
        if not _allowlisted(url, config):
            raise ToolError(f"Host not in HTTP_ALLOWLIST: {urlparse(url).hostname}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text[:12000]

    if name == "list_models":
        client = LLMClient(config)
        try:
            tags = client.list_tags()
            names = [str(t.get("name") or t.get("model")) for t in tags]
            return json.dumps(names, indent=2)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(str(exc)) from exc

    if name == "search_logs":
        query = str(args.get("query") or "").lower()
        limit = min(int(args.get("limit") or 5), 20)
        hits: list[str] = []
        log_dir = config.log_dir
        if log_dir.exists():
            for path in sorted(log_dir.glob("*.md"), reverse=True):
                text = path.read_text(encoding="utf-8", errors="replace")
                if query in text.lower():
                    hits.append(f"{path.name}: {text[:400]}")
                if len(hits) >= limit:
                    break
        return "\n\n".join(hits) if hits else "No matching logs."

    raise ToolError(f"Unknown tool: {name}")
