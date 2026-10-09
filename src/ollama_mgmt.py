"""Best-effort Ollama VRAM helpers (unload before heavy SDXL jobs)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from src.config import Config

logger = logging.getLogger(__name__)


def unload_ollama_models(config: Config | None = None) -> list[str]:
    """Ask Ollama to drop loaded models from VRAM (`keep_alive=0`).

    Returns names we attempted to unload. Failures are logged, never raised —
    image gen should still proceed if Ollama is down.
    """
    config = config or Config.from_env()
    base = (config.llm_base_url or "").rstrip("/")
    if not base:
        return []
    unloaded: list[str] = []
    try:
        tags = requests.get(f"{base}/api/tags", timeout=3)
        tags.raise_for_status()
        models = [m.get("name") for m in (tags.json().get("models") or []) if m.get("name")]
    except Exception as exc:  # noqa: BLE001
        logger.info("Ollama unload skipped (tags): %s", exc)
        return []

    # Prefer unloading whatever is currently resident if /api/ps exists.
    names: list[str] = []
    try:
        ps = requests.get(f"{base}/api/ps", timeout=3)
        if ps.ok:
            names = [
                m.get("name") or m.get("model")
                for m in (ps.json().get("models") or [])
                if (m.get("name") or m.get("model"))
            ]
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        # Fall back: unload configured heavy roles (vision/strong) if present locally.
        prefer = {
            (config.model_vision or "").strip(),
            (config.model_strong or "").strip(),
            (config.llm_model or "").strip(),
        }
        names = [n for n in prefer if n and n in set(models)]

    for name in names:
        try:
            requests.post(
                f"{base}/api/generate",
                json={"model": name, "keep_alive": 0, "prompt": ""},
                timeout=8,
            )
            unloaded.append(name)
            logger.info("Unloaded Ollama model from VRAM: %s", name)
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not unload %s: %s", name, exc)
    return unloaded


def ollama_reachable(config: Config | None = None) -> bool:
    config = config or Config.from_env()
    try:
        r = requests.get(f"{config.llm_base_url.rstrip('/')}/api/tags", timeout=2)
        return r.ok
    except Exception:  # noqa: BLE001
        return False
