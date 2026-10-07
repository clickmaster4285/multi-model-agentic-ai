"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    max_active_jobs_per_user: int
    queue_depth_limit: int
    overflow_wait_seconds: float
    model_fast: str
    model_strong: str
    model_cloud: str
    model_vision: str
    model_image: str
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
            max_active_jobs_per_user=max(1, int(os.getenv("MAX_ACTIVE_JOBS_PER_USER", "1"))),
            queue_depth_limit=max(1, int(os.getenv("QUEUE_DEPTH_LIMIT", "40"))),
            overflow_wait_seconds=float(os.getenv("OVERFLOW_WAIT_SECONDS", "45")),
            model_fast=os.getenv("MODEL_FAST", "qwen2.5:3b"),
            model_strong=os.getenv("MODEL_STRONG", "llama3.1:latest"),
            model_cloud=os.getenv("MODEL_CLOUD", ""),
            model_vision=os.getenv("MODEL_VISION", "llava:7b"),
            model_image=os.getenv("MODEL_IMAGE", ""),
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
        return Config(
            llm_base_url=self.llm_base_url,
            llm_model=model,
            temperature=self.temperature,
            timeout_seconds=self.timeout_seconds,
            log_dir=self.log_dir,
            database_url=self.database_url,
            jwt_secret=self.jwt_secret,
            jwt_expire_minutes=self.jwt_expire_minutes,
            llm_slots=self.llm_slots,
            max_active_jobs_per_user=self.max_active_jobs_per_user,
            queue_depth_limit=self.queue_depth_limit,
            overflow_wait_seconds=self.overflow_wait_seconds,
            model_fast=self.model_fast,
            model_strong=self.model_strong,
            model_cloud=self.model_cloud,
            model_vision=self.model_vision,
            model_image=self.model_image,
            remote_llm_base_url=self.remote_llm_base_url,
            remote_llm_api_key=self.remote_llm_api_key,
            http_allowlist=self.http_allowlist,
            artifacts_dir=self.artifacts_dir,
            sso_enabled=self.sso_enabled,
            default_admin_user=self.default_admin_user,
            default_admin_password=self.default_admin_password,
            api_host=self.api_host,
            api_port=self.api_port,
        )
