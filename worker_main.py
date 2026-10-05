#!/usr/bin/env python3
"""Standalone horizontal worker process."""

from __future__ import annotations

from src.config import Config
from src.db import init_db
from src.worker import worker_loop


def main() -> None:
    config = Config.from_env()
    init_db(config)
    print(f"MulteAgent worker started (llm_slots={config.llm_slots})")
    print(f"Database: {config.database_url}")
    worker_loop(config)


if __name__ == "__main__":
    main()
