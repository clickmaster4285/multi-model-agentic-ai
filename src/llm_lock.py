"""Process-wide LLM concurrency gate for shared GPU fairness."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from src.config import Config

_lock: threading.BoundedSemaphore | None = None
_lock_slots: int | None = None
_meta = threading.Lock()


def get_llm_semaphore(config: Config | None = None) -> threading.BoundedSemaphore:
    global _lock, _lock_slots
    config = config or Config.from_env()
    with _meta:
        if _lock is None or _lock_slots != config.llm_slots:
            _lock = threading.BoundedSemaphore(value=config.llm_slots)
            _lock_slots = config.llm_slots
        return _lock


@contextmanager
def llm_slot(config: Config | None = None):
    sem = get_llm_semaphore(config)
    sem.acquire()
    try:
        yield
    finally:
        sem.release()
