"""Thin HTTP client for Ollama and OpenAI-compatible chat APIs."""

from __future__ import annotations

import time
from typing import Any

import requests

from src.config import Config
from src.llm_lock import llm_slot


class LLMError(RuntimeError):
    """Raised when the LLM cannot complete a request."""


def _llm_err(exc: BaseException | None) -> str:
    """Short, readable cause (avoid truncated 'HTTPConnectionPool…')."""
    if exc is None:
        return "unknown"
    if isinstance(exc, requests.ConnectionError):
        return "connection refused — Ollama is not reachable"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}: {(exc.response.text or '')[:160]}"
    msg = str(exc).strip() or type(exc).__name__
    return msg if len(msg) <= 220 else msg[:217] + "…"


class LLMClient:
    def __init__(self, config: Config, *, model: str | None = None, backend: str | None = None) -> None:
        self.config = config.with_model(model) if model else config
        self.backend = backend or self._infer_backend(self.config.llm_model)

    def _infer_backend(self, model: str) -> str:
        if model.endswith(":cloud") or model.startswith("cloud:"):
            return "remote_openai_compatible"
        if self.config.remote_llm_base_url and model.startswith("remote:"):
            return "remote_openai_compatible"
        return "local_ollama"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_retries: int = 2,
        model: str | None = None,
        images: list[str] | None = None,
    ) -> str:
        cfg = self.config.with_model(model) if model else self.config
        backend = self._infer_backend(cfg.llm_model)
        temp = cfg.temperature if temperature is None else temperature
        pics = [img for img in (images or []) if img]

        with llm_slot(cfg):
            if backend == "remote_openai_compatible":
                return self._chat_openai_compatible(cfg, system, user, temp, max_retries, pics)
            return self._chat_ollama(cfg, system, user, temp, max_retries, pics)

    def _chat_ollama(
        self,
        cfg: Config,
        system: str,
        user: str,
        temperature: float,
        max_retries: int,
        images: list[str] | None = None,
    ) -> str:
        url = f"{cfg.llm_base_url}/api/chat"
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = images
        payload: dict[str, Any] = {
            "model": cfg.llm_model,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": cfg.llm_num_ctx},
            "messages": [
                {"role": "system", "content": system},
                user_msg,
            ],
        }
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(url, json=payload, timeout=cfg.timeout_seconds)
                response.raise_for_status()
                data = response.json()
                content = data.get("message", {}).get("content")
                if not content or not str(content).strip():
                    raise LLMError("Local LLM returned an empty response.")
                return str(content).strip()
            except (requests.RequestException, ValueError, LLMError) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise LLMError(
            f"Failed to reach local LLM at {url} (model={cfg.llm_model}). "
            f"Last error: {_llm_err(last_error)}. Is Ollama running? (ollama serve)"
        )

    def _chat_openai_compatible(
        self,
        cfg: Config,
        system: str,
        user: str,
        temperature: float,
        max_retries: int,
        images: list[str] | None = None,
    ) -> str:
        base = cfg.remote_llm_base_url or cfg.llm_base_url
        # Ollama cloud models can still use local /v1 endpoint
        if not cfg.remote_llm_base_url and cfg.llm_model.endswith(":cloud"):
            base = cfg.llm_base_url
        url = f"{base}/v1/chat/completions"
        model_name = cfg.llm_model.removeprefix("remote:")
        headers = {"Content-Type": "application/json"}
        if cfg.remote_llm_api_key:
            headers["Authorization"] = f"Bearer {cfg.remote_llm_api_key}"
        if images:
            content: Any = [{"type": "text", "text": user}]
            for img in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img}"},
                    }
                )
            user_content = content
        else:
            user_content = user
        payload = {
            "model": model_name,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    url, json=payload, headers=headers, timeout=cfg.timeout_seconds
                )
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError("Remote LLM returned no choices.")
                content = choices[0].get("message", {}).get("content")
                if not content or not str(content).strip():
                    raise LLMError("Remote LLM returned an empty response.")
                return str(content).strip()
            except (requests.RequestException, ValueError, LLMError, IndexError, KeyError) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise LLMError(f"Failed remote LLM at {url} (model={model_name}). Last error: {last_error}")

    def ping(self) -> bool:
        try:
            response = requests.get(f"{self.config.llm_base_url}/api/tags", timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException:
            if self.config.remote_llm_base_url:
                try:
                    response = requests.get(
                        f"{self.config.remote_llm_base_url}/models",
                        headers=(
                            {"Authorization": f"Bearer {self.config.remote_llm_api_key}"}
                            if self.config.remote_llm_api_key
                            else {}
                        ),
                        timeout=10,
                    )
                    return response.ok
                except requests.RequestException:
                    return False
            return False

    def list_tags(self) -> list[dict[str, Any]]:
        response = requests.get(f"{self.config.llm_base_url}/api/tags", timeout=15)
        response.raise_for_status()
        return list(response.json().get("models") or [])

    def show_model(self, name: str) -> dict[str, Any]:
        response = requests.post(
            f"{self.config.llm_base_url}/api/show",
            json={"model": name},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def generate_image(self, *, prompt: str, model: str, size: str = "1024x1024") -> bytes:
        """Ollama experimental OpenAI-compatible image API (`/v1/images/generations`)."""
        import base64

        cfg = self.config.with_model(model)
        url = f"{cfg.llm_base_url}/v1/images/generations"
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        try:
            response = requests.post(url, json=payload, timeout=cfg.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(
                f"Image generation failed at {url} (model={model}). {exc}"
            ) from exc
        data = response.json() if response.content else {}
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise LLMError("Image API returned no image data.")
        b64 = str(rows[0].get("b64_json") or "").strip()
        if not b64:
            raise LLMError("Image API returned empty b64_json.")
        try:
            blob = base64.b64decode(b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise LLMError("Image API returned invalid base64.") from exc
        if not blob:
            raise LLMError("Decoded image was empty.")
        return blob
