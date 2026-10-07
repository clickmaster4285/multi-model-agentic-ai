"""Text-to-image via in-process Diffusers SDXL (local checkpoint)."""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Callable

from src.attachments import save_images
from src.config import Config
from src.llm_lock import llm_slot
from src.sdxl_pipeline import (
    ImageGenCudaError,
    ImageGenNotConfigured,
    generate as sdxl_generate,
)

ProgressCallback = Callable[[dict[str, Any]], None]

NO_CHECKPOINT = """This request needs **local SDXL image generation**.

Set `MODEL_IMAGE_PATH` in `.env` to your SDXL checkpoint (`.safetensors`), for example:

```
MODEL_IMAGE_PATH=D:/multeagent/models/checkpoints/sd_xl_base_1.0.safetensors
```

On Quadro P4000 / Pascal GPUs install the CUDA 11.8 PyTorch stack:

```
pip install -r requirements-image-cu118.txt
```

Then restart the API and try again. Vision models (`llava`) can only **look at** images — they cannot draw new ones."""


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
            "sdxl-local",
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
    label = (
        (model_plan.get("image") or config.model_image or "sdxl-local")
    ).strip() or "sdxl-local"
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
            "role": "Image generation",
            "stage": "chat",
            "accent": "#c4a35a",
            "model": label,
            "job_id": job_id,
        },
    )

    output = NO_CHECKPOINT
    path = config.model_image_path
    if path is not None and path.is_file():
        try:
            with llm_slot(config):
                image = sdxl_generate(query, config=config)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            png = buf.getvalue()
            stored = save_images(
                config,
                job_id,
                [
                    {
                        "filename": "generated.png",
                        "mime": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    }
                ],
            )
            for row in stored:
                row["kind"] = "generated"
            saved = stored
            output = (
                f"Generated with local SDXL (`{label}`) "
                f"at {config.image_width}×{config.image_height}, "
                f"{config.image_steps} steps."
            )
        except ImageGenNotConfigured as exc:
            errors.append(str(exc))
            output = str(exc)
        except ImageGenCudaError as exc:
            errors.append(str(exc))
            output = str(exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            output = (
                f"Image generation failed: {exc}\n\n"
                "On 8GB GPUs keep IMAGE_WIDTH/IMAGE_HEIGHT at 768 (not 1024). "
                "Unload large Ollama models before generating. "
                "Pascal GPUs need: pip install -r requirements-image-cu118.txt"
            )
    else:
        errors.append("MODEL_IMAGE_PATH not set or file missing")

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
            "images": [
                {"filename": a["filename"], "mime": a.get("mime") or "image/png"}
                for a in saved
            ],
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
