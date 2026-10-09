"""In-process notify so SSE can wake immediately when events are appended."""

from __future__ import annotations

import threading
from collections import defaultdict

_meta = threading.Lock()
_waiters: dict[str, threading.Event] = defaultdict(threading.Event)


def notify_job(job_id: str) -> None:
    with _meta:
        ev = _waiters[job_id]
        ev.set()


def wait_job(job_id: str, timeout: float = 0.35) -> bool:
    """Wait until notify or timeout. Returns True if notified."""
    with _meta:
        ev = _waiters[job_id]
        ev.clear()
    return ev.wait(timeout=timeout)


def clear_job(job_id: str) -> None:
    with _meta:
        _waiters.pop(job_id, None)
