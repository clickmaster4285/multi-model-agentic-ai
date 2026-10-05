#!/usr/bin/env python3
"""CLI entrypoint for the local multi-agent consensus system."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.config import Config
from src.pipeline import run_debate
from src.runner import run_session


DEFAULT_QUERY = (
    "Should our mid-size logistics company adopt an AI-assisted route planner "
    "for last-mile delivery in Q3?"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Optimist/Cynic/Consensus debate, or launch the control-deck GUI.",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=DEFAULT_QUERY,
        help="Business question to debate (default: sample logistics query).",
    )
    parser.add_argument("--model", help="Override LLM model name.")
    parser.add_argument(
        "--temperature",
        type=float,
        help="Override sampling temperature.",
    )
    parser.add_argument(
        "--base-url",
        help="Override LLM base URL (default: http://localhost:11434).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Directory for timestamped debate logs.",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run panel agents concurrently (may stress 8GB VRAM).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the Python API (jobs + auth + in-process worker)",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Run a standalone horizontal worker process",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the final consensus output.",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    base = Config.from_env()
    data = base.__dict__.copy()
    data["llm_base_url"] = (args.base_url or base.llm_base_url).rstrip("/")
    data["llm_model"] = args.model or base.llm_model
    if args.temperature is not None:
        data["temperature"] = args.temperature
    if args.log_dir is not None:
        data["log_dir"] = args.log_dir
    return Config(**data)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.gui:
        from src.config import Config as EnvConfig

        cfg = EnvConfig.from_env()
        print(f"Starting MulteAgent API at http://{cfg.api_host}:{cfg.api_port}")
        print("In another terminal: cd frontend && npm run dev")
        print("Default login: admin / admin123")
        from web.server import main as gui_main

        gui_main()
        return 0

    if args.worker:
        from worker_main import main as worker_main

        worker_main()
        return 0

    config = build_config(args)
    mode = "parallel" if args.parallel else "sequential"

    print(f"Model : {config.llm_model}")
    print(f"LLM   : {config.llm_base_url}")
    print(f"Mode  : {mode}")
    print(f"Query : {args.query}\n")
    print(f"Running agents ({mode})...\n")

    try:
        result = run_session(args.query, mode=mode, config=config)
    except Exception as exc:  # noqa: BLE001 — surface clean CLI errors
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.quiet:
        if result.consensus:
            print(result.consensus.output)
        return 0 if not result.errors else 1

    for agent_result in result.panel:
        print("=" * 60)
        print(f"{agent_result.name.upper()} ({agent_result.elapsed_seconds:.1f}s)")
        print("=" * 60)
        print(agent_result.output)
        print()

    if result.consensus:
        print("=" * 60)
        print(f"{result.consensus.name.upper()} ({result.consensus.elapsed_seconds:.1f}s)")
        print("=" * 60)
        print(result.consensus.output)
        print()

    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f" - {err}")
        print()

    print(f"Log   : {result.log_path}")
    print(f"Total : {result.total_seconds:.1f}s")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
