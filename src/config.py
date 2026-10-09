"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_PROFILE_DEFAULTS = {
    "fast": {"steps": 16, "guidance": 5.5, "strength": 0.42},
    "quality": {"steps": 28, "guidance": 7.0, "strength": 0.35},
    "balanced": {"steps": 20, "guidance": 6.5, "strength": 0.4},
}


@dataclass(frozen=True)
class Config:
    llm_base_url: str
    llm_model: str
    temperature: float
    timeout_seconds: int
    log_dir: Path
    database_url: str
    jwt_secret: str
    jwt_expire_minutes: int
    llm_slots: int
    image_slots: int
    max_active_jobs_per_user: int
    queue_depth_limit: int
    overflow_wait_seconds: float
    model_fast: str
    model_strong: str
    model_cloud: str
    model_vision: str
    model_image: str
    model_image_path: Path | None
    image_width: int
    image_height: int
    image_steps: int
    image_guidance: float
    image_strength: float
    image_negative_prompt: str
    image_prompt_polish: bool
    image_profile: str
    image_warm_on_start: bool
    image_unload_ollama: bool
    image_tiny_vae: bool
    remote_llm_base_url: str
    remote_llm_api_key: str
    http_allowlist: str
    artifacts_dir: Path
    sso_enabled: bool
    default_admin_user: str
    default_admin_password: str
    api_host: str
    api_port: int

    @classmethod
    def from_env(cls) -> Config:
        data_dir = ROOT / "data"
        default_db = f"sqlite:///{(data_dir / 'multeagent.db').as_posix()}"
        default_ckpt = ROOT / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
        raw_image_path = (os.getenv("MODEL_IMAGE_PATH") or "").strip()
        if raw_image_path:
            image_path = Path(raw_image_path)
        elif default_ckpt.is_file():
            image_path = default_ckpt
        else:
            image_path = None

        profile = (os.getenv("IMAGE_PROFILE") or "fast").strip().lower()
        if profile not in _PROFILE_DEFAULTS:
            profile = "fast"
        pdata = _PROFILE_DEFAULTS[profile]

        steps_raw = os.getenv("IMAGE_STEPS")
        guidance_raw = os.getenv("IMAGE_GUIDANCE")
        strength_raw = os.getenv("IMAGE_STRENGTH")

        return cls(
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "llama3.1:latest"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.4")),
            timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
            log_dir=Path(os.getenv("LOG_DIR", str(ROOT / "logs"))),
            database_url=os.getenv("DATABASE_URL", default_db),
            jwt_secret=os.getenv(
                "JWT_SECRET",
                "change-me-multeagent-dev-secret-32b+",
            ),
            jwt_expire_minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "720")),
            llm_slots=max(1, int(os.getenv("LLM_SLOTS", "1"))),
            image_slots=max(1, int(os.getenv("IMAGE_SLOTS", "1"))),
            max_active_jobs_per_user=max(1, int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "1"))),
            queue_depth_limit=max(1, int(os.getenv("QUEUE_DEPTH_LIMIT", "40"))),
            overflow_wait_seconds=float(os.getenv("OVERFLOW_WAIT_SECONDS", "45")),
            model_fast=os.getenv("MODEL_FAST", "qwen2.5:3b"),
            model_strong=os.getenv("MODEL_STRONG", "llama3.1:latest"),
            model_cloud=os.getenv("MODEL_CLOUD", ""),
            model_vision=os.getenv("MODEL_VISION", "llava:7b"),
            model_image=os.getenv("MODEL_IMAGE", "sdxl-local"),
            model_image_path=image_path,
            image_width=max(256, int(os.getenv("IMAGE_WIDTH", "768"))),
            image_height=max(256, int(os.getenv("IMAGE_HEIGHT", "768"))),
            image_steps=max(
                1, int(steps_raw) if steps_raw else int(pdata["steps"])
            ),
            image_guidance=float(
                guidance_raw if guidance_raw else pdata["guidance"]
            ),
            image_strength=max(
                0.05,
                min(
                    1.0,
                    float(strength_raw if strength_raw else pdata["strength"]),
                ),
            ),
            image_negative_prompt=os.getenv(
                "IMAGE_NEGATIVE_PROMPT",
                "lowres, blurry, distorted, watermark, text, logo, deformed, ugly",
            ),
            image_prompt_polish=_bool("IMAGE_PROMPT_POLISH", True),
            image_profile=profile,
            image_warm_on_start=_bool("IMAGE_WARM_ON_START", True),
            image_unload_ollama=_bool("IMAGE_UNLOAD_OLLAMA", True),
            image_tiny_vae=_bool("IMAGE_TINY_VAE", False),
            remote_llm_base_url=os.getenv("REMOTE_LLM_BASE_URL", "").rstrip("/"),
            remote_llm_api_key=os.getenv("REMOTE_LLM_API_KEY", ""),
            http_allowlist=os.getenv(
                "HTTP_ALLOWLIST",
                "localhost,127.0.0.1,ollama.com,raw.githubusercontent.com",
            ),
            artifacts_dir=Path(os.getenv("ARTIFACTS_DIR", str(ROOT / "artifacts"))),
            sso_enabled=_bool("SSO_ENABLED", False),
            default_admin_user=os.getenv("DEFAULT_ADMIN_USER", "admin"),
            default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8787")),
        )

    def with_model(self, model: str | None) -> Config:
        if not model:
            return self
        return replace(self, llm_model=model)

    def with_image_overrides(
        self,
        *,
        steps: int | None = None,
        guidance: float | None = None,
        strength: float | None = None,
        profile: str | None = None,
    ) -> Config:
        """Apply per-job image overrides (UI presets / profiles)."""
        prof = (profile or "").strip().lower() or None
        pdata = _PROFILE_DEFAULTS.get(prof) if prof else None
        return replace(
            self,
            image_profile=prof or self.image_profile,
            image_steps=max(
                1,
                int(
                    steps
                    if steps is not None
                    else (pdata["steps"] if pdata else self.image_steps)
                ),
            ),
            image_guidance=float(
                guidance
                if guidance is not None
                else (pdata["guidance"] if pdata else self.image_guidance)
            ),
            image_strength=max(
                0.05,
                min(
                    1.0,
                    float(
                        strength
                        if strength is not None
                        else (pdata["strength"] if pdata else self.image_strength)
                    ),
                ),
            ),
        )
