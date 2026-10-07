"""Text-to-image via Ollama's experimental OpenAI-compatible images API."""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

from src.attachments import save_images
from src.config import Config
from src.llm_client import LLMClient, LLMError
from src.llm_lock import llm_slot

ProgressCallback = Callable[[dict[str, Any]], None]

NO_IMAGE_MODEL = """This request needs an **image generation** model.

`llava` and other **· vision** models can **look at** a picture you attach. They cannot **draw** a new one. Chat models only write text, which is why you got a Midjourney-style prompt instead of a seahorse.

None of the models currently loaded in Ollama generate images. Pull a generator, then retry:

```
ollama pull x/z-image-turbo
```

Then pick it in the model menu (it will show **· image gen**) or leave Auto model route on."""


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def looks_like_image_gen_model(name: str) -> bool:
    lower = (name or "").lower()
    return any(
        token in lower
        for token in (
            "z-image",
            "zimage",
            "flux",
            "sdxl",
            "stable-diffusion",
            "stable_diffusion",
            "sd3",
            "imagen",
            "dall-e",
            "dalle",
            "gpt-image",
        )
    )


def run_image_gen(
    query: str,
    *,
    job_id: str,
    config: Config | None = None,
    model_plan: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, list[dict[str, str]], list[str], float]:
    """Return (message, saved attachment dicts, errors, seconds)."""
    config = config or Config.from_env()
    model_plan = model_plan or {}
    model = (model_plan.get("image") or config.model_image or "").strip()
    started = time.perf_counter()
    errors: list[str] = []
    saved: list[dict[str, str]] = []

    _emit(on_progress, {"type": "session_start", "mode": "chat", "query": query})
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": "Image generation" if model else "Direct reply",
            "stage": "chat",
            "accent": "#c4a35a",
            "model": model or None,
            "job_id": job_id,
        },
    )

    output = NO_IMAGE_MODEL
    if model:
        client = LLMClient(config)
        try:
            with llm_slot(config):
                png = client.generate_image(prompt=query, model=model)
            stored = save_images(
                config,
                job_id,
                [{"filename": "generated.png", "mime": "image/png", "data": base64.b64encode(png).decode("ascii")}],
            )
            for row in stored:
                row["kind"] = "generated"
            saved = stored
            output = f"Generated with `{model}`."
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            output = (
                f"Image generation failed with `{model}`: {exc}\n\n"
                "Vision/chat models cannot draw pictures. Use an image-generation model "
                "such as `x/z-image-turbo`."
            )

    elapsed = time.perf_counter() - started
    _emit(
        on_progress,
        {
            "type": "agent_done",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": "Image generation" if saved else "Direct reply",
            "stage": "chat",
            "accent": "#c4a35a",
            "elapsed_seconds": elapsed,
            "output": output,
            "job_id": job_id,
            "images": [{"filename": a["filename"], "mime": a.get("mime") or "image/png"} for a in saved],
        },
    )
    _emit(
        on_progress,
        {
            "type": "session_done",
            "total_seconds": elapsed,
            "errors": errors,
            "mode": "chat",
        },
    )
    return output, saved, errors, elapsed
