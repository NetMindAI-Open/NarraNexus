"""
@file_name: narranexus_migrate.py
@author: NetMind.AI
@date: 2026-07-21
@description: Thin dev CLI over the Agent Migration Scanner (local, read-only).

Usage:
    uv run python scripts/dev/narranexus_migrate.py detect
    uv run python scripts/dev/narranexus_migrate.py scan [--path ~/.claude] [--framework claude_code]

detect  -> list every framework found in the standard home locations.
scan    -> detect + extract one source into the standardized JSON (stdout).

Read-only. Never writes to NarraNexus, never prints non-MCP secret VALUES.
"""

import argparse
import json
import sys

from xyz_agent_context.migration import detect, scan


def main() -> int:
    ap = argparse.ArgumentParser(prog="narranexus-migrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect", help="list frameworks found in standard home locations")
    sp = sub.add_parser("scan", help="detect + extract one source into standardized JSON")
    sp.add_argument("--path", default=None, help="source dir (default: auto-detect)")
    sp.add_argument("--framework", default=None,
                    choices=["claude_code", "hermes", "openclaw", "codex", "custom"])
    args = ap.parse_args()

    if args.cmd == "detect":
        dets = detect()
        if not dets:
            print("No known agent framework found in the standard home locations.")
            return 0
        for d in dets:
            print(f"  {d.framework:<12} {d.confidence:<7} {d.path}")
            print(f"       signals: {', '.join(d.signals)}")
        return 0

    # scan
    try:
        result = scan(path=args.path, framework=args.framework)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
