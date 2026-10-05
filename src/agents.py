"""Persona wrappers around the shared LLM client."""

from __future__ import annotations

from dataclasses import dataclass

from src.llm_client import LLMClient
from src import prompts


@dataclass
class AgentResult:
    name: str
    role: str
    output: str
    elapsed_seconds: float


class Agent:
    def __init__(self, name: str, role: str, system_prompt: str, client: LLMClient) -> None:
        self.name = name
        self.role = role
        self.system_prompt = system_prompt
        self.client = client

    def run(self, user_message: str) -> AgentResult:
        import time

        started = time.perf_counter()
        output = self.client.chat(system=self.system_prompt, user=user_message)
        elapsed = time.perf_counter() - started
        return AgentResult(
            name=self.name,
            role=self.role,
            output=output,
            elapsed_seconds=elapsed,
        )


def build_agents(client: LLMClient) -> dict[str, Agent]:
    return {
        "optimist": Agent(
            name="Agent 1",
            role="Visionary / Optimist",
            system_prompt=prompts.OPTIMIST_SYSTEM,
            client=client,
        ),
        "cynic": Agent(
            name="Agent 2",
            role="Cynic / Risk Analyst",
            system_prompt=prompts.CYNIC_SYSTEM,
            client=client,
        ),
        "consensus": Agent(
            name="Agent 3",
            role="Executive Consensus Engine",
            system_prompt=prompts.CONSENSUS_SYSTEM,
            client=client,
        ),
    }
