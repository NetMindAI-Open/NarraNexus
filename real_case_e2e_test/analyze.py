"""
@file_name: analyze.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: CLI entrypoint — re-run the semantic phase on an existing
              run directory without re-driving the agents

Useful when the first run skipped semantic (no claude CLI, or
--skip-semantic) and you want to add verdicts after the fact, or when
the prompt template was updated.

Usage:
    python -m real_case_e2e_test.analyze reports/20260513_140502/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from real_case_e2e_test.core.semantic import build_prompt, run_semantic


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def _analyze(run_dir: Path, timeout_seconds: int) -> int:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"[analyze] missing manifest.json under {run_dir}", file=sys.stderr)
        return 2
    manifest = _load_json(manifest_path)

    sem_dir = run_dir / "semantic"
    sem_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for case_row in manifest.get("cases", []):
        case_id = case_row["case_id"]
        safe_id = case_id.replace("/", "__")
        transcript_path = run_dir / "transcripts" / f"{safe_id}.json"
        metrics_path = run_dir / "programmatic" / f"{safe_id}.json"
        if not transcript_path.exists() or not metrics_path.exists():
            print(f"[analyze] skipping {case_id}: transcript or metrics missing")
            failed += 1
            continue
        transcript = _load_json(transcript_path)
        metrics = _load_json(metrics_path)
        case_spec = {
            "case_id": case_id,
            "pillar": case_row.get("pillar"),
            "description": transcript.get("description"),
            "linked_bugs": case_row.get("linked_bugs", []),
            "severity": case_row.get("severity"),
            "tags": transcript.get("tags", []),
            "semantic_intent": transcript.get("semantic_intent", ""),
        }
        # reconstruct talk lines from the transcript (turn-by-turn)
        talk = [
            {
                "role": t.get("role"),
                "content": t.get("input_content"),
                "expect_contains": t.get("expect_contains", []),
                "expect_not_contains": t.get("expect_not_contains", []),
            }
            for t in transcript.get("turns", [])
        ]
        prompt = build_prompt(
            case_spec=case_spec,
            talk=talk,
            transcript=transcript,
            metrics=metrics,
        )
        print(f"[analyze] {case_id}")
        result = await run_semantic(case_id=case_id, prompt=prompt, timeout_seconds=timeout_seconds)
        out_path = sem_dir / f"{safe_id}.md"
        body_parts = []
        if result.skipped:
            body_parts.append(f"# semantic skipped — {result.reason}")
        elif result.error:
            body_parts.append(f"# semantic errored — {result.error}")
        if result.verdict_markdown:
            body_parts.append(result.verdict_markdown)
        out_path.write_text("\n\n".join(body_parts))
    print(f"[analyze] done; semantic outputs in {sem_dir}")
    return 0 if not failed else 1


def _parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="real_case_e2e_test.analyze")
    p.add_argument("run_dir", help="path to reports/<run_ts>/")
    p.add_argument("--timeout", type=int, default=180)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(list(sys.argv[1:]) if argv is None else argv)
    return asyncio.run(_analyze(Path(args.run_dir).resolve(), args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
