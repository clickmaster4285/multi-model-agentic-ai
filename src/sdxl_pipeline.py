"""Process-wide SDXL Diffusers pipeline (singleton, Pascal/8GB-safe)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pipeline: Any | None = None
_pipeline_path: str | None = None


class ImageGenNotConfigured(RuntimeError):
    """Raised when MODEL_IMAGE_PATH is missing or unreadable."""


class ImageGenCudaError(RuntimeError):
    """Raised when the GPU build cannot run kernels (e.g. wrong CUDA wheel)."""


def _check_cuda_kernels() -> None:
    """Fail fast if torch CUDA has no kernels for this GPU (Pascal + wrong wheel)."""
    import torch

    if not torch.cuda.is_available():
        logger.warning("CUDA not available; SDXL will run on CPU (very slow).")
        return
    try:
        x = torch.zeros(1, device="cuda", dtype=torch.float16)
        _ = x + 1
        del x
        torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001
        raise ImageGenCudaError(
            "CUDA kernels failed on this GPU. Quadro P4000 / Pascal (sm_61) needs "
            "PyTorch cu118, not cu124/cu130. Install with:\n"
            "  pip install -r requirements-image-cu118.txt\n"
            f"Original error: {exc}"
        ) from exc


def get_pipeline(config: Config | None = None) -> Any:
    """Load StableDiffusionXLPipeline once per process; reuse thereafter."""
    global _pipeline, _pipeline_path
    config = config or Config.from_env()
    path = config.model_image_path
    if path is None or not Path(path).is_file():
        raise ImageGenNotConfigured(
            "Image generation is not configured. Set MODEL_IMAGE_PATH to your "
            "SDXL `.safetensors` file (e.g. models/checkpoints/sd_xl_base_1.0.safetensors), "
            "then restart the API."
        )
    resolved = str(Path(path).resolve())
    with _lock:
        if _pipeline is not None and _pipeline_path == resolved:
            return _pipeline
        if _pipeline is not None:
            logger.info("Reloading SDXL pipeline from %s", resolved)
            _pipeline = None
            _pipeline_path = None

        import torch
        from diffusers import StableDiffusionXLPipeline, UniPCMultistepScheduler

        _check_cuda_kernels()
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        logger.info("Loading SDXL from %s (dtype=%s)", resolved, dtype)
        pipe = StableDiffusionXLPipeline.from_single_file(
            resolved,
            torch_dtype=dtype,
            use_safetensors=True,
        )
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
        if getattr(pipe, "watermark", None) is not None:
            pipe.watermark = None
        # Offload owns device placement — do not call pipe.to("cuda") before this.
        if torch.cuda.is_available():
            pipe.enable_model_cpu_offload()
        # Diffusers 0.41+: VAE slicing lives on the VAE module, not the pipeline.
        if hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        elif getattr(pipe, "vae", None) is not None and hasattr(pipe.vae, "enable_slicing"):
            pipe.vae.enable_slicing()
        _pipeline = pipe
        _pipeline_path = resolved
        logger.info("SDXL pipeline ready")
        return _pipeline


def generate(
    prompt: str,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
) -> Any:
    """Run text-to-image; returns a PIL.Image.Image."""
    config = config or Config.from_env()
    pipe = get_pipeline(config)
    w = width if width is not None else config.image_width
    h = height if height is not None else config.image_height
    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )
    # Keep multiples of 8 for VAE
    w = max(256, (int(w) // 8) * 8)
    h = max(256, (int(h) // 8) * 8)
    result = pipe(
        prompt=prompt,
        negative_prompt=neg or None,
        width=w,
        height=h,
        num_inference_steps=n_steps,
        guidance_scale=guide,
    )
    return result.images[0]


def reset_pipeline() -> None:
    """Drop cached pipeline (tests / model path change)."""
    global _pipeline, _pipeline_path
    with _lock:
        _pipeline = None
        _pipeline_path = None
