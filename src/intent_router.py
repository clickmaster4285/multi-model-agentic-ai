"""Fast intent router: decide chat vs debate vs agentic vs vision vs image gen."""

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
IMAGE_GEN_RE = re.compile(
    r"\b(create|generate|draw|paint|render|make|design|imagine)\b.{0,60}\b"
    r"(image|picture|photo|illustration|artwork|drawing|logo|icon|poster)\b|"
    r"\b(image|picture|photo|illustration)\s+of\b|"
    r"\btext[- ]to[- ]image\b|"
    r"\b(ui\s*/?\s*ux|ux\s*/?\s*ui|dashboard|mockup|wireframe|landing\s+page|"
    r"app\s+screen|mobile\s+ui|web\s+ui)\b|"
    r"\b(design|make|create|generate|render|build)\b.{0,80}\b"
    r"(dashboard|mockup|wireframe|landing\s+page|interface|layout|ui|ux)\b|"
    r"\b(photorealistic|photo[- ]realistic|cinematic|8k|4k|ultra[- ]detailed|"
    r"highly detailed|masterpiece|octane render|unreal engine|concept art|"
    r"portrait of|landscape of)\b",
    re.I,
)
IMAGE_EDIT_RE = re.compile(
    r"\b(regenerate|remix|restyle|img2img|image[- ]to[- ]image|inpaint)\b|"
    r"\b(edit|modify|change|replace|swap|update)\b.{0,80}\b"
    r"(image|picture|photo|screenshot|this|it|text|label|title|logo)\b|"
    r"\b(only\s+change|change\s+.+\s+to\b|don'?t\s+change\s+anything\s+else|"
    r"keep\s+(the\s+)?(same|layout|rest)|based\s+on\s+this|"
    r"using\s+this\s+(image|picture|photo|screenshot))\b",
    re.I,
)
VISION_ASK_RE = re.compile(
    r"\b(what('s|\s+is|\s+are)|whats|describe|explain|summarize|read|ocr|"
    r"transcribe|analyze|identify|caption|look\s+at|tell\s+me\s+about)\b|"
    r"\b(in|on)\s+this\s+(image|picture|photo|screenshot)\b|"
    r"\b(this\s+image|this\s+picture|this\s+screenshot)\b",
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
{"intent":"chat"|"creative"|"debate"|"agentic"|"image_gen"|"vision","confidence":0.0-1.0,"image_mode":"txt2img"|"img2img"|null}

Rules:
- chat: greetings, thanks, chitchat, simple Q&A
- creative: stories, poems, jokes, fiction
- debate: business/strategy decisions needing pros/cons/consensus
- agentic: tools, research, browsing, multi-step repo/file work
- image_gen: draw/generate/edit a picture (set image_mode=txt2img or img2img)
- vision: user attached/asks about an existing image to describe/read (not redraw)
Prefer chat when unsure and the message is short. Prefer heuristics-style certainty."""


@dataclass(frozen=True)
class RouteDecision:
    requested_mode: str
    resolved_mode: str
    intent: str
    reason: str
    used_llm: bool = False
    confidence: float = 1.0
    model: str | None = None
    image_mode: str | None = None  # txt2img | img2img | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.resolved_mode,
            "intent": self.intent,
            "reason": self.reason,
            "used_llm": self.used_llm,
            "confidence": self.confidence,
            "model": self.model,
            "image_mode": self.image_mode,
        }

    def with_model(self, model: str) -> RouteDecision:
        return RouteDecision(
            requested_mode=self.requested_mode,
            resolved_mode=self.resolved_mode,
            intent=self.intent,
            reason=f"{self.reason} ({model})",
            used_llm=self.used_llm,
            confidence=self.confidence,
            model=model,
            image_mode=self.image_mode,
        )


def _normalize_mode(mode: str | None) -> str:
    m = (mode or "auto").strip().lower()
    if m in MANUAL_MODES or m == "auto":
        return m
    return "auto"


def _image_gen_decision(
    requested: str,
    *,
    reason: str,
    image_mode: str,
    used_llm: bool = False,
    confidence: float = 1.0,
) -> RouteDecision:
    return RouteDecision(
        requested_mode=requested,
        resolved_mode="chat",
        intent="image_gen",
        reason=reason,
        used_llm=used_llm,
        confidence=confidence,
        image_mode=image_mode,
    )


def _route_with_images(text: str, requested: str, config: Config) -> RouteDecision:
    """Decide vision vs img2img vs txt2img when the user attached files."""
    wants_edit = bool(IMAGE_EDIT_RE.search(text) or IMAGE_GEN_RE.search(text))
    wants_vision = bool(VISION_ASK_RE.search(text))

    if wants_edit and not wants_vision:
        return _image_gen_decision(
            requested,
            reason="attachment + draw/edit -> img2img",
            image_mode="img2img",
        )
    if wants_edit and wants_vision:
        # "describe then regenerate" / mixed → prefer edit when regenerate/change present
        if IMAGE_EDIT_RE.search(text):
            return _image_gen_decision(
                requested,
                reason="attachment + mixed ask, edit wins -> img2img",
                image_mode="img2img",
            )
    if wants_vision or not text or text.lower() in {"what's in this image?", "whats in this image?"}:
        vision = (config.model_vision or "").strip() or None
        return RouteDecision(
            requested_mode=requested,
            resolved_mode="chat",
            intent="vision",
            reason="attachment + describe/read ask -> vision",
            used_llm=False,
            confidence=1.0,
            model=vision,
        )
    # Attached image + short vague edit like "make it darker"
    if len(text) < 160 and re.search(
        r"\b(make|change|turn|convert|update|fix|improve|brighter|darker|blue|red)\b",
        text,
        re.I,
    ):
        return _image_gen_decision(
            requested,
            reason="attachment + transform phrasing -> img2img",
            image_mode="img2img",
            confidence=0.85,
        )
    vision = (config.model_vision or "").strip() or None
    return RouteDecision(
        requested_mode=requested,
        resolved_mode="chat",
        intent="vision",
        reason="attachment default -> vision",
        used_llm=False,
        confidence=0.7,
        model=vision,
    )


def _heuristic(query: str) -> RouteDecision | None:
    text = query.strip()
    if not text:
        return RouteDecision("auto", "chat", "chat", "empty message -> chat")

    if GREETING_RE.match(text) or len(text) <= 12 and not DEBATE_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "greeting / short chitchat -> chat")

    if IMAGE_GEN_RE.search(text) or IMAGE_EDIT_RE.search(text):
        return _image_gen_decision(
            "auto",
            reason="image generation request -> txt2img",
            image_mode="txt2img",
        )

    if CREATIVE_RE.search(text):
        return RouteDecision("auto", "chat", "creative", "creative writing -> chat")

    if AGENTIC_RE.search(text):
        return RouteDecision("auto", "agentic", "agentic", "tools / research signals -> agentic")

    if DEBATE_RE.search(text) and len(text) >= 40:
        return RouteDecision("auto", "debate", "debate", "decision / business signals -> debate")

    if len(text) < 80 and not DEBATE_RE.search(text) and not AGENTIC_RE.search(text):
        return RouteDecision("auto", "chat", "chat", "short question -> chat")

    return None


def _parse_llm_classify(raw: str) -> tuple[str, float, str | None] | None:
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
    image_mode = data.get("image_mode")
    image_mode_s = str(image_mode).strip().lower() if image_mode else None
    if image_mode_s not in {"txt2img", "img2img"}:
        image_mode_s = None
    if intent == "creative":
        return "chat", conf, None
    if intent == "vision":
        return "vision", conf, None
    if intent == "image_gen":
        return "image_gen", conf, image_mode_s or "txt2img"
    if intent in RESOLVED_MODES:
        return intent, conf, None
    return None


def _llm_classify(query: str, config: Config) -> RouteDecision | None:
    from src.llm_client import LLMClient, LLMError

    model = config.model_fast or config.llm_model
    try:
        raw = LLMClient(config).chat(
            system=CLASSIFY_SYSTEM,
            user=query[:1200],
            model=model,
            temperature=0.0,
            max_retries=0,
        )
    except (LLMError, Exception):
        return None
    parsed = _parse_llm_classify(raw)
    if not parsed:
        return None
    intent_mode, conf, image_mode = parsed
    if intent_mode == "image_gen":
        return _image_gen_decision(
            "auto",
            reason="fast-model classify -> image_gen",
            image_mode=image_mode or "txt2img",
            used_llm=True,
            confidence=conf,
        )
    if intent_mode == "vision":
        vision = (config.model_vision or "").strip() or None
        return RouteDecision(
            requested_mode="auto",
            resolved_mode="chat",
            intent="vision",
            reason="fast-model classify -> vision",
            used_llm=True,
            confidence=conf,
            model=vision,
        )
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
    has_images: bool = False,
) -> RouteDecision:
    """Resolve execution mode and optional image_mode (txt2img/img2img)."""
    config = config or Config.from_env()
    requested = _normalize_mode(requested_mode)
    text = query.strip()

    if has_images:
        return _route_with_images(text, requested, config)

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
        if IMAGE_GEN_RE.search(text) or IMAGE_EDIT_RE.search(text):
            return _image_gen_decision(
                requested,
                reason="image generation bypasses debate board",
                image_mode="txt2img",
            )
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

    # Only call the fast LLM when heuristics are truly ambiguous — keeps Auto snappy.
    if allow_llm and len(text) >= 40:
        llm_hit = _llm_classify(query, config)
        if llm_hit is not None and llm_hit.confidence >= 0.55:
            return llm_hit

    if len(text) >= 160 and DEBATE_RE.search(text):
        return RouteDecision(
            "auto",
            "debate",
            "debate",
            "long decision-shaped query -> debate",
            used_llm=False,
            confidence=0.45,
        )
    return RouteDecision(
        "auto",
        "chat",
        "chat",
        "ambiguous query -> chat",
        used_llm=False,
        confidence=0.4,
    )
