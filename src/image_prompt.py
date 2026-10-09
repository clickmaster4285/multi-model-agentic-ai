"""Fast local prompt cleanup for SDXL (no LLM round-trip)."""

from __future__ import annotations

import re

_PREFIX_RE = re.compile(
    r"^(please\s+)?("
    r"create|generate|draw|paint|render|make|design|imagine|regenerate"
    r")(\s+(me|an?|the))?\s+"
    r"(an?\s+)?(image|picture|photo|illustration|artwork|drawing)\s+(of|with|for)?\s*",
    re.I,
)
_THIS_RE = re.compile(
    r"\b(of\s+this|from\s+this|based\s+on\s+this|using\s+this)\b"
    r"(\s+(image|picture|photo|screenshot))?",
    re.I,
)
_NOISE_RE = re.compile(
    r"\b(then\s+regenerate(\s+image)?|don'?t\s+change\s+anything\s+else|"
    r"and\s+then\s+regenerate(\s+the)?\s*(image)?)\b",
    re.I,
)
_RICH_TAGS = (
    "photorealistic",
    "cinematic",
    "8k",
    "4k",
    "highly detailed",
    "masterpiece",
    "octane",
    "unreal engine",
    "volumetric",
    "sharp focus",
)


def should_polish(query: str, *, img2img: bool = False) -> bool:
    """Skip enrichment when the user already wrote a rich SDXL-style prompt."""
    text = (query or "").strip()
    if not text:
        return False
    if img2img:
        return True  # always add keep-layout hints for edits
    lower = text.lower()
    rich_hits = sum(1 for t in _RICH_TAGS if t in lower)
    if rich_hits >= 2 and len(text) >= 80:
        return False
    if len(text) >= 220:
        return False
    return True


def polish_prompt(query: str, *, img2img: bool = False) -> str:
    """Turn chat phrasing into a tighter SDXL prompt without calling an LLM."""
    text = (query or "").strip()
    if not text:
        return "high quality detailed image"
    text = _PREFIX_RE.sub("", text).strip(" ,.-")
    text = _THIS_RE.sub("", text)
    text = _NOISE_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.-")
    if not text:
        text = "high quality detailed image"

    lower = text.lower()
    if img2img:
        if "same" not in lower and "keep" not in lower:
            text = (
                f"{text}, same composition and layout as the reference image, "
                "only apply the requested changes"
            )
    else:
        if len(text) < 120 and not any(t in lower for t in _RICH_TAGS):
            text = f"{text}, highly detailed, sharp focus, natural lighting"

    return text[:800]
