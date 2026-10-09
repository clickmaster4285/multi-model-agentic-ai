"""Process-wide concurrency gates for shared GPU fairness."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from src.config import Config

_llm_lock: threading.BoundedSemaphore | None = None
_llm_slots: int | None = None
_image_lock: threading.BoundedSemaphore | None = None
_image_slots: int | None = None
_meta = threading.Lock()


def get_llm_semaphore(config: Config | None = None) -> threading.BoundedSemaphore:
    global _llm_lock, _llm_slots
    config = config or Config.from_env()
    with _meta:
        if _llm_lock is None or _llm_slots != config.llm_slots:
            _llm_lock = threading.BoundedSemaphore(value=config.llm_slots)
            _llm_slots = config.llm_slots
        return _llm_lock


def get_image_semaphore(config: Config | None = None) -> threading.BoundedSemaphore:
    global _image_lock, _image_slots
    config = config or Config.from_env()
    slots = getattr(config, "image_slots", None) or config.llm_slots
    with _meta:
        if _image_lock is None or _image_slots != slots:
            _image_lock = threading.BoundedSemaphore(value=slots)
            _image_slots = slots
        return _image_lock


@contextmanager
def llm_slot(config: Config | None = None):
    """Exclusive slot for Ollama / remote LLM calls."""
    sem = get_llm_semaphore(config)
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


@contextmanager
def image_slot(config: Config | None = None):
    """Exclusive slot for local SDXL. Also holds llm_slot so vision/chat wait."""
    config = config or Config.from_env()
    llm = get_llm_semaphore(config)
    img = get_image_semaphore(config)
    llm.acquire()
    try:
        img.acquire()
        try:
            yield
        finally:
            img.release()
    finally:
        llm.release()
