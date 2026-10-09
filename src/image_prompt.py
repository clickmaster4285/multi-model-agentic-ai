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
        # Light quality boost for short scene prompts (skip if already rich).
        if len(text) < 120 and not any(
            t in lower for t in ("photorealistic", "cinematic", "8k", "highly detailed")
        ):
            text = f"{text}, highly detailed, sharp focus, natural lighting"

    return text[:800]
