"""Persistent agent definitions (create / update / delete)."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from src.config import ROOT
from src import prompts

Stage = Literal["panel", "consensus"]
AGENTS_DIR = ROOT / "agents"
AGENTS_FILE = AGENTS_DIR / "agents.json"


@dataclass
class AgentDef:
    id: str
    name: str
    role: str
    system_prompt: str
    stage: Stage = "panel"
    enabled: bool = True
    sort_order: int = 0
    accent: str = "#c9852a"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDef:
        stage = data.get("stage", "panel")
        if stage not in ("panel", "consensus"):
            stage = "panel"
        return cls(
            id=str(data["id"]),
            name=str(data["name"]).strip(),
            role=str(data.get("role", "")).strip(),
            system_prompt=str(data.get("system_prompt", "")).strip(),
            stage=stage,  # type: ignore[arg-type]
            enabled=bool(data.get("enabled", True)),
            sort_order=int(data.get("sort_order", 0)),
            accent=str(data.get("accent", "#c9852a")),
        )


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned or "agent"


def default_agents() -> list[AgentDef]:
    return [
        AgentDef(
            id="optimist",
            name="Optimist",
            role="Visionary / Upside",
            system_prompt=prompts.OPTIMIST_SYSTEM,
            stage="panel",
            enabled=True,
            sort_order=10,
            accent="#2f9e6b",
        ),
        AgentDef(
            id="cynic",
            name="Cynic",
            role="Risk Analyst",
            system_prompt=prompts.CYNIC_SYSTEM,
            stage="panel",
            enabled=True,
            sort_order=20,
            accent="#c44b3c",
        ),
        AgentDef(
            id="consensus",
            name="Consensus",
            role="Executive Decision",
            system_prompt=prompts.CONSENSUS_SYSTEM,
            stage="consensus",
            enabled=True,
            sort_order=100,
            accent="#c9852a",
        ),
    ]


class AgentStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or AGENTS_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save_all(default_agents())

    def load_all(self) -> list[AgentDef]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        agents = [AgentDef.from_dict(item) for item in raw]
        return sorted(agents, key=lambda a: (a.sort_order, a.name.lower()))

    def save_all(self, agents: list[AgentDef]) -> None:
        payload = [a.to_dict() for a in sorted(agents, key=lambda a: (a.sort_order, a.name.lower()))]
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def get(self, agent_id: str) -> AgentDef | None:
        for agent in self.load_all():
            if agent.id == agent_id:
                return agent
        return None

    def create(self, data: dict[str, Any]) -> AgentDef:
        agents = self.load_all()
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Agent name is required.")
        system_prompt = str(data.get("system_prompt", "")).strip()
        if not system_prompt:
            raise ValueError("System prompt is required.")

        agent_id = str(data.get("id") or _slug(name))
        if any(a.id == agent_id for a in agents):
            agent_id = f"{agent_id}-{uuid.uuid4().hex[:6]}"

        agent = AgentDef.from_dict(
            {
                "id": agent_id,
                "name": name,
                "role": data.get("role", ""),
                "system_prompt": system_prompt,
                "stage": data.get("stage", "panel"),
                "enabled": data.get("enabled", True),
                "sort_order": data.get(
                    "sort_order",
                    max((a.sort_order for a in agents), default=0) + 10,
                ),
                "accent": data.get("accent", "#6b8cae"),
            }
        )
        agents.append(agent)
        self.save_all(agents)
        return agent

    def update(self, agent_id: str, data: dict[str, Any]) -> AgentDef:
        agents = self.load_all()
        for index, agent in enumerate(agents):
            if agent.id != agent_id:
                continue
            merged = agent.to_dict()
            for key in ("name", "role", "system_prompt", "stage", "enabled", "sort_order", "accent"):
                if key in data:
                    merged[key] = data[key]
            merged["id"] = agent_id
            updated = AgentDef.from_dict(merged)
            if not updated.name.strip():
                raise ValueError("Agent name is required.")
            if not updated.system_prompt.strip():
                raise ValueError("System prompt is required.")
            agents[index] = updated
            self.save_all(agents)
            return updated
        raise KeyError(f"Agent not found: {agent_id}")

    def delete(self, agent_id: str) -> None:
        agents = self.load_all()
        remaining = [a for a in agents if a.id != agent_id]
        if len(remaining) == len(agents):
            raise KeyError(f"Agent not found: {agent_id}")
        self.save_all(remaining)

    def reset_defaults(self) -> list[AgentDef]:
        agents = default_agents()
        self.save_all(agents)
        return agents
