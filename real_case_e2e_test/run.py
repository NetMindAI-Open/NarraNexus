"""
@file_name: run.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: CLI entrypoint — python -m real_case_e2e_test.run

Argv → RunnerConfig → core.runner.execute.
Exit codes:
  0  every case passed the programmatic gate
  1  at least one case failed
  2  preflight refused to start (stack down / claude missing)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace

from real_case_e2e_test.core.runner import RunnerConfig, execute


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="real_case_e2e_test.run")
    p.add_argument("--base-url", default=os.environ.get("NN_E2E_BASE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--ws-url", default=os.environ.get("NN_E2E_WS_URL", "ws://127.0.0.1:8000"))
    p.add_argument("--concurrency", type=int, default=int(os.environ.get("NN_E2E_CONCURRENCY", "5")))
    p.add_argument("--pillar", default=None, help="run only one pillar (folder under cases/)")
    p.add_argument("--case", default=None, help="substring match against case_id")
    p.add_argument("--list", action="store_true", help="discover and list cases without running")
    p.add_argument("--skip-semantic", action="store_true", help="skip the Claude Code semantic phase")
    p.add_argument(
        "--inter-group-sleep",
        type=float,
        default=float(os.environ.get("NN_E2E_INTER_GROUP_SLEEP", "15")),
        help="seconds to sleep between pillars (helps providers drain rate limit)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(list(sys.argv[1:]) if argv is None else argv)
    config = RunnerConfig(
        base_url=args.base_url,
        ws_url=args.ws_url,
        concurrency=args.concurrency,
        skip_semantic=args.skip_semantic,
        inter_group_sleep_seconds=args.inter_group_sleep,
    )
    return asyncio.run(execute(config, args.pillar, args.case, args.list))


if __name__ == "__main__":
    raise SystemExit(main())
