"""Process-wide SDXL Diffusers pipelines (txt2img + img2img, Pascal/8GB-safe)."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_txt2img: Any | None = None
_img2img: Any | None = None
_pipeline_path: str | None = None
_warm_state: str = "cold"  # cold | warming | ready | error
_warm_error: str | None = None


class ImageGenNotConfigured(RuntimeError):
    """Raised when MODEL_IMAGE_PATH is missing or unreadable."""


class ImageGenCudaError(RuntimeError):
    """Raised when the GPU build cannot run kernels (e.g. wrong CUDA wheel)."""


def pipeline_status() -> dict[str, Any]:
    return {
        "state": _warm_state,
        "error": _warm_error,
        "loaded": _txt2img is not None and _img2img is not None,
        "path": _pipeline_path,
    }


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


def _enable_memory_savers(pipe: Any) -> None:
    import torch

    if torch.cuda.is_available():
        pipe.enable_model_cpu_offload()
    if hasattr(pipe, "enable_vae_slicing"):
        pipe.enable_vae_slicing()
    elif getattr(pipe, "vae", None) is not None and hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()


def _maybe_tiny_vae(pipe: Any, config: Config, dtype: Any) -> None:
    if not config.image_tiny_vae:
        return
    try:
        from diffusers import AutoencoderTiny

        logger.info("Swapping in TinyVAE (taesdxl) for faster decode")
        pipe.vae = AutoencoderTiny.from_pretrained(
            "madebyollin/taesdxl",
            torch_dtype=dtype,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("TinyVAE unavailable, keeping default VAE: %s", exc)


def _load_pipelines(resolved: str, config: Config) -> tuple[Any, Any]:
    import torch
    from diffusers import (
        StableDiffusionXLImg2ImgPipeline,
        StableDiffusionXLPipeline,
        UniPCMultistepScheduler,
    )

    _check_cuda_kernels()
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    logger.info("Loading SDXL from %s (dtype=%s)", resolved, dtype)
    txt = StableDiffusionXLPipeline.from_single_file(
        resolved,
        torch_dtype=dtype,
        use_safetensors=True,
    )
    txt.scheduler = UniPCMultistepScheduler.from_config(txt.scheduler.config)
    if getattr(txt, "watermark", None) is not None:
        txt.watermark = None
    _maybe_tiny_vae(txt, config, dtype)
    # Share weights — do not load the 7GB checkpoint twice.
    img = StableDiffusionXLImg2ImgPipeline(**txt.components)
    img.scheduler = txt.scheduler
    if getattr(img, "watermark", None) is not None:
        img.watermark = None
    # Offload owns placement — never .to("cuda") before this.
    _enable_memory_savers(txt)
    logger.info("SDXL txt2img + img2img ready (profile=%s)", config.image_profile)
    return txt, img


def get_pipelines(config: Config | None = None) -> tuple[Any, Any]:
    """Return (txt2img, img2img) singletons for this process."""
    global _txt2img, _img2img, _pipeline_path, _warm_state, _warm_error
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
        if _txt2img is not None and _img2img is not None and _pipeline_path == resolved:
            _warm_state = "ready"
            return _txt2img, _img2img
        if _txt2img is not None:
            logger.info("Reloading SDXL pipelines from %s", resolved)
            _txt2img = None
            _img2img = None
            _pipeline_path = None
        try:
            _txt2img, _img2img = _load_pipelines(resolved, config)
            _pipeline_path = resolved
            _warm_state = "ready"
            _warm_error = None
        except Exception as exc:  # noqa: BLE001
            _warm_state = "error"
            _warm_error = str(exc)
            raise
        return _txt2img, _img2img


def get_pipeline(config: Config | None = None) -> Any:
    """Backward-compatible: return txt2img pipeline."""
    return get_pipelines(config)[0]


def warm_pipeline_async(config: Config | None = None) -> None:
    """Background warm-load so the first chat image is not cold."""
    global _warm_state, _warm_error
    config = config or Config.from_env()
    if not config.image_warm_on_start:
        return
    if config.model_image_path is None or not Path(config.model_image_path).is_file():
        logger.info("SDXL warm skipped: no checkpoint configured")
        return

    def _run() -> None:
        global _warm_state, _warm_error
        _warm_state = "warming"
        try:
            get_pipelines(config)
            logger.info("SDXL warm-load complete — image model ready")
        except Exception as exc:  # noqa: BLE001
            _warm_state = "error"
            _warm_error = str(exc)
            logger.warning("SDXL warm-load failed: %s", exc)

    threading.Thread(target=_run, name="sdxl-warm", daemon=True).start()


def _dims(config: Config, width: int | None, height: int | None) -> tuple[int, int]:
    w = width if width is not None else config.image_width
    h = height if height is not None else config.image_height
    return max(256, (int(w) // 8) * 8), max(256, (int(h) // 8) * 8)


def _step_callback(should_continue: Any | None):
    if should_continue is None:
        return None

    def _cb(pipe: Any, step_index: int, timestep: Any, callback_kwargs: dict) -> dict:
        if not should_continue():
            pipe._interrupt = True
        return callback_kwargs

    return _cb


def generate(
    prompt: str,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
) -> Any:
    """Run text-to-image; returns a PIL.Image.Image."""
    config = config or Config.from_env()
    pipe, _ = get_pipelines(config)
    w, h = _dims(config, width, height)
    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )
    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": neg or None,
        "width": w,
        "height": h,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    result = pipe(**kwargs)
    return result.images[0]


def generate_from_image(
    prompt: str,
    init_image: Any,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    strength: float | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
) -> Any:
    """Run image-to-image; returns a PIL.Image.Image."""
    from PIL import Image

    config = config or Config.from_env()
    _, pipe = get_pipelines(config)
    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    strength_v = strength if strength is not None else config.image_strength
    strength_v = max(0.05, min(1.0, float(strength_v)))
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )

    image = init_image
    if not isinstance(image, Image.Image):
        image = Image.open(image).convert("RGB")
    else:
        image = image.convert("RGB")

    target_w, target_h = _dims(config, None, None)
    image = _fit_image(image, target_w, target_h)

    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "image": image,
        "negative_prompt": neg or None,
        "strength": strength_v,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    result = pipe(**kwargs)
    return result.images[0]


def generate_inpaint(
    prompt: str,
    init_image: Any,
    mask_image: Any,
    *,
    config: Config | None = None,
    negative_prompt: str | None = None,
    strength: float | None = None,
    steps: int | None = None,
    guidance: float | None = None,
    should_continue: Any | None = None,
) -> Any:
    """Inpaint masked regions; mask white = repaint, black = keep."""
    from PIL import Image
    from diffusers import StableDiffusionXLInpaintPipeline

    config = config or Config.from_env()
    txt, _ = get_pipelines(config)
    # Build inpaint pipe from shared components (no second 7GB load).
    pipe = StableDiffusionXLInpaintPipeline(**txt.components)
    pipe.scheduler = txt.scheduler

    n_steps = steps if steps is not None else config.image_steps
    guide = guidance if guidance is not None else config.image_guidance
    strength_v = strength if strength is not None else max(config.image_strength, 0.75)
    strength_v = max(0.05, min(1.0, float(strength_v)))
    neg = (
        negative_prompt
        if negative_prompt is not None
        else config.image_negative_prompt
    )

    image = init_image if isinstance(init_image, Image.Image) else Image.open(init_image)
    image = image.convert("RGB")
    mask = mask_image if isinstance(mask_image, Image.Image) else Image.open(mask_image)
    mask = mask.convert("L")
    target_w, target_h = _dims(config, None, None)
    image = _fit_image(image, target_w, target_h)
    mask = _fit_image(mask.convert("RGB"), target_w, target_h).convert("L")

    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "image": image,
        "mask_image": mask,
        "negative_prompt": neg or None,
        "strength": strength_v,
        "num_inference_steps": n_steps,
        "guidance_scale": guide,
        "width": target_w,
        "height": target_h,
    }
    cb = _step_callback(should_continue)
    if cb is not None:
        kwargs["callback_on_step_end"] = cb
    result = pipe(**kwargs)
    return result.images[0]


def _fit_image(image: Any, width: int, height: int) -> Any:
    from PIL import Image

    src_w, src_h = image.size
    if src_w == width and src_h == height:
        return image
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = max(0, (new_w - width) // 2)
    top = max(0, (new_h - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def reset_pipeline() -> None:
    """Drop cached pipelines (tests / model path change)."""
    global _txt2img, _img2img, _pipeline_path, _warm_state, _warm_error
    with _lock:
        _txt2img = None
        _img2img = None
        _pipeline_path = None
        _warm_state = "cold"
        _warm_error = None
