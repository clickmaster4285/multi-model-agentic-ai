"""Pre-Ollama intent router: pick chat vs debate vs agentic without a full multi-agent run."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.config import Config

MANUAL_MODES = frozenset({"debate", "agentic", "mixed", "chat"})
RESOLVED_MODES = frozenset({"chat", "debate", "agentic"})

GREETING_RE = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|hiya|good\s*(morning|afternoon|evening)|thanks|thank\s*you|"
    r"thx|ty|bye|goodbye|see\s*ya|ok|okay|k|cool|nice|great|awesome)[\s!.?]*$",
    re.I,
)
CREATIVE_RE = re.compile(
    r"\b(write|compose|draft|create|make|tell)\b.+\b(story|poem|joke|song|riddle|haiku|essay|"
    r"script|dialogue|fairy\s*tale|fable)\b|"
    r"\b(story|poem|joke|haiku)\b.+\b(about|on|with)\b|"
    r"\bwith\s+emojis?\b",
    re.I,
)
AGENTIC_RE = re.compile(
    r"\b(research|browse|fetch|scrape|http|url|website|repo|codebase|file|artifact|"
    r"multi[- ]?step|use\s+tools?|look\s+up|investigate|crawl)\b|"
    r"https?://",
    re.I,
)
DEBATE_RE = re.compile(
    r"\b(should\s+we|pros?\s+and\s+cons?|trade[- ]?offs?|risks?|feasib|decide|decision|"
    r"recommend(ation)?|strategy|invest|expand|launch|business|market|compliance|"
    r"consensus|evaluate|go\s*/\s*no[- ]?go|pilot\s+first)\b",
    re.I,
)

CLASSIFY_SYSTEM = """You classify user messages for a multi-agent system.
Reply with ONLY compact JSON, no markdown:
{"intent":"chat"|"creative"|"debate"|"agentic","confidence":0.0-1.0}

Rules:
- chat: greetings, thanks, chitchat, simple Q&A, explanations that need one reply
- creative: stories, poems, jokes, fiction, emoji writing
- debate: business/strategy decisions needing pros/cons/risks/consensus
- agentic: needs tools, research, browsing, multi-step repo/file work
Prefer chat when unsure and the message is short."""


@dataclass(frozen=True)
class RouteDecision:
    requested_mode: str
    resolved_mode: str
    intent: str
    reason: str
    used_llm: bool = False
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.resolved_mode,
            "intent": self.intent,
            "reason": self.reason,
            "used_llm": self.used_llm,
            "confidence": self.confidence,
        }


def _normalize_mode(mode: str | None) -> str:
    m = (mode or "auto").strip().lower()
    if m in MANUAL_MODES or m == "auto":
        return m
    return "auto"


def _heuristic(query: str) -> RouteDecision | None:
    text = query.strip()
    if not text:
        return RouteDecision("auto", "chat", "chat", "empty message -> chat")

    if GREETING_RE.match(text) or len(text) <= 12 and not DEBATE_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "greeting / short chitchat -> chat")

    if CREATIVE_RE.search(text):
        return RouteDecision("auto", "chat", "creative", "creative writing -> chat")

    if AGENTIC_RE.search(text):
        return RouteDecision("auto", "agentic", "agentic", "tools / research signals -> agentic")

    if DEBATE_RE.search(text) and len(text) >= 40:
        return RouteDecision("auto", "debate", "debate", "decision / business signals -> debate")

    # Short informational asks: single reply
    if len(text) < 80 and not DEBATE_RE.search(text) and not AGENTIC_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "short question -> chat")

    return None


def _parse_llm_classify(raw: str) -> tuple[str, float] | None:
    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    intent = str(data.get("intent") or "").strip().lower()
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    if intent == "creative":
        return "chat", conf
    if intent in RESOLVED_MODES:
        return intent, conf
    return None


def _llm_classify(query: str, config: Config) -> RouteDecision | None:
    from src.llm_client import LLMClient, LLMError

    model = config.model_fast or config.llm_model
    try:
        raw = LLMClient(config).chat(
            system=CLASSIFY_SYSTEM,
            user=query[:2000],
            model=model,
            temperature=0.0,
            max_retries=0,
        )
    except (LLMError, Exception):
        return None
    parsed = _parse_llm_classify(raw)
    if not parsed:
        return None
    intent_mode, conf = parsed
    return RouteDecision(
        requested_mode="auto",
        resolved_mode=intent_mode,
        intent="creative" if "creative" in raw.lower() and intent_mode == "chat" else intent_mode,
        reason=f"fast-model classify -> {intent_mode}",
        used_llm=True,
        confidence=conf,
    )


def classify_query(
    query: str,
    *,
    requested_mode: str | None = "auto",
    config: Config | None = None,
    allow_llm: bool = True,
) -> RouteDecision:
    """Resolve execution mode.

    Clear greetings (and creative asks left on Debate) always use chat so the
    business board is never wasted on "hi".
    """
    config = config or Config.from_env()
    requested = _normalize_mode(requested_mode)
    text = query.strip()

    # Hard bypass: never run Optimist/Cynic/Consensus (or agentic tools) on "hi"
    if GREETING_RE.match(text) or (len(text) <= 8 and text.isalpha()):
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="chat",
            reason="greeting always uses chat",
            used_llm=False,
            confidence=1.0,
        )

    if requested in MANUAL_MODES:
        # Debate selected + creative ask -> still chat (business personas are wrong)
        if requested in {"debate", "mixed"} and CREATIVE_RE.search(text):
            return RouteDecision(
                requested_mode=requested,
                resolved_mode="chat",
                intent="creative",
                reason="creative ask bypasses debate board",
                used_llm=False,
                confidence=1.0,
            )
        return RouteDecision(
            requested_mode=requested,
            resolved_mode=requested,
            intent=requested,
            reason="manual mode override",
            used_llm=False,
            confidence=1.0,
        )

    hit = _heuristic(query)
    if hit is not None:
        return hit

    if allow_llm:
        llm_hit = _llm_classify(query, config)
        if llm_hit is not None and llm_hit.confidence >= 0.45:
            return llm_hit

    # Safe defaults by length when still ambiguous
    if len(text) >= 160:
        return RouteDecision(
            "auto",
            "debate",
            "debate",
            "ambiguous long query -> debate",
            used_llm=False,
            confidence=0.4,
        )
    return RouteDecision(
        "auto",
        "chat",
        "chat",
        "ambiguous short/mid query -> chat",
        used_llm=False,
        confidence=0.4,
    )
