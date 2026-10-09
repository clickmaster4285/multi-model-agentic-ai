"""Text-to-image and image-to-image via in-process Diffusers SDXL."""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Callable

from src.attachments import load_mask_image, load_pil_images, save_images
from src.config import Config
from src.image_prompt import polish_prompt, should_polish
from src.llm_lock import image_slot
from src.ollama_mgmt import unload_ollama_models
from src.sdxl_pipeline import (
    ImageGenCudaError,
    ImageGenNotConfigured,
    generate as sdxl_generate,
    generate_from_image as sdxl_img2img,
    generate_inpaint as sdxl_inpaint,
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
    attachments: list[dict[str, Any]] | None = None,
    image_mode: str | None = None,
    should_continue: Callable[[], bool] | None = None,
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
    attachments = attachments or []

    mode = (image_mode or "").strip().lower()
    init_images = load_pil_images(config, attachments) if attachments else []
    mask = load_mask_image(config, attachments) if attachments else None
    use_inpaint = mode == "inpaint" or (mode == "img2img" and mask is not None)
    use_img2img = (not use_inpaint) and (
        mode == "img2img" or (bool(init_images) and mode not in {"txt2img", "inpaint"})
    )
    if use_inpaint:
        role = "Image edit (inpaint)"
    elif use_img2img:
        role = "Image edit (img2img)"
    else:
        role = "Image generation"

    _emit(on_progress, {"type": "session_start", "mode": "chat", "query": query})
    _emit(
        on_progress,
        {
            "type": "agent_start",
            "agent_id": "assistant",
            "name": "Assistant",
            "role": role,
            "stage": "chat",
            "accent": "#c4a35a",
            "model": label,
            "job_id": job_id,
        },
    )

    raw_query = (query or "").strip()
    needs_edit_polish = use_img2img or use_inpaint
    prompt = (
        polish_prompt(raw_query, img2img=needs_edit_polish)
        if config.image_prompt_polish
        and should_polish(raw_query, img2img=needs_edit_polish)
        else raw_query
    )

    output = NO_CHECKPOINT
    path = config.model_image_path
    if path is not None and path.is_file():
        try:
            if config.image_unload_ollama:
                unloaded = unload_ollama_models(config)
                if unloaded:
                    _emit(
                        on_progress,
                        {
                            "type": "status",
                            "message": f"Freed Ollama VRAM: {', '.join(unloaded)}",
                        },
                    )
            with image_slot(config):
                if use_inpaint:
                    if not init_images:
                        raise ValueError("Inpaint needs a source image attachment.")
                    if mask is None:
                        raise ValueError(
                            "Inpaint needs a mask image (filename containing 'mask'; "
                            "white = areas to repaint). Or use Edit (img2img) without a mask."
                        )
                    image = sdxl_inpaint(
                        prompt,
                        init_images[0],
                        mask,
                        config=config,
                        should_continue=should_continue,
                    )
                elif use_img2img:
                    if not init_images:
                        raise ValueError(
                            "img2img requested but no usable attached image was found."
                        )
                    image = sdxl_img2img(
                        prompt,
                        init_images[0],
                        config=config,
                        should_continue=should_continue,
                    )
                else:
                    image = sdxl_generate(
                        prompt,
                        config=config,
                        should_continue=should_continue,
                    )
            if should_continue is not None and not should_continue():
                raise RuntimeError("Image generation cancelled.")
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
            if use_inpaint:
                output = (
                    f"Inpainted with local SDXL (`{label}`, profile={config.image_profile}) "
                    f"at {config.image_width}×{config.image_height}, "
                    f"{config.image_steps} steps, strength {config.image_strength:.2f}."
                )
            elif use_img2img:
                output = (
                    f"Edited with local SDXL img2img (`{label}`, profile={config.image_profile}) "
                    f"at {config.image_width}×{config.image_height}, "
                    f"{config.image_steps} steps, strength {config.image_strength:.2f}."
                )
            else:
                output = (
                    f"Generated with local SDXL (`{label}`, profile={config.image_profile}) "
                    f"at {config.image_width}×{config.image_height}, "
                    f"{config.image_steps} steps."
                )
            if prompt != raw_query:
                output += f"\n\nPrompt used: `{prompt}`"
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
            "role": role if saved else "Direct reply",
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
