"""
@file_name: semantic.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Wrap the local ``claude`` CLI to produce a semantic verdict

We shell out to ``claude -p`` and feed it a prompt assembled from:
  - the static template at prompts/semantic_per_case.md
  - the case SPEC + TALK
  - the per-case transcript JSON
  - the programmatic metrics JSON

The CLI's stdout is treated as the verdict markdown verbatim. We do not
try to parse it — the report assembler embeds it as-is. If the CLI is
missing or non-zero, we record the failure and continue (semantic is
best-effort; programmatic is the hard gate).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "semantic_per_case.md"
)


@dataclass
class SemanticResult:
    case_id: str
    verdict_markdown: str
    error: str | None = None
    skipped: bool = False
    reason: str = ""


def build_prompt(
    *,
    case_spec: dict[str, Any],
    talk: list[dict[str, Any]],
    transcript: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    sections = [
        template.rstrip(),
        "",
        "## Case spec",
        "```json",
        json.dumps(case_spec, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Talk script",
        "```json",
        json.dumps(talk, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Transcript",
        "```json",
        json.dumps(transcript, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Programmatic metrics",
        "```json",
        json.dumps(metrics, indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(sections)


async def run_semantic(
    case_id: str,
    prompt: str,
    *,
    claude_binary: str = "claude",
    timeout_seconds: int = 180,
) -> SemanticResult:
    """Invoke the Claude Code CLI with the assembled prompt, capture stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_binary,
            "-p",
            "--output-format",
            "text",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return SemanticResult(
            case_id=case_id,
            verdict_markdown="",
            skipped=True,
            reason=f"{claude_binary!r} not on PATH",
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return SemanticResult(
            case_id=case_id,
            verdict_markdown="",
            error=f"claude CLI exceeded {timeout_seconds}s",
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        return SemanticResult(
            case_id=case_id,
            verdict_markdown=stdout,
            error=f"claude CLI exit {proc.returncode}; stderr: {stderr.strip()[:300]}",
        )

    return SemanticResult(case_id=case_id, verdict_markdown=stdout)
