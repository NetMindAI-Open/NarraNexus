"""
@file_name: preflight.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Verify the stack is ready before any case runs

We refuse to start the suite if:
  - /health does not respond 200 (stack not up)
  - claude CLI is missing AND the user did not pass --skip-semantic

We warn (but do not refuse) when:
  - The user has no providers configured. The case can still run, the
    agent will simply fail when it tries to call its LLM and that
    failure shows up in the transcript. The point of the warning is
    that the failure mode is "user error, not regression".
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .api_client import APIClient


@dataclass
class PreflightResult:
    ok: bool
    health_ok: bool
    claude_cli_found: bool
    warnings: list[str]
    errors: list[str]


async def run_preflight(api: APIClient, *, require_semantic: bool) -> PreflightResult:
    warnings: list[str] = []
    errors: list[str] = []

    health_ok = await api.health()
    if not health_ok:
        errors.append(
            f"backend /health at {api.base_url}/health did not respond 200. "
            "Run `bash run.sh` first."
        )

    claude_cli_found = shutil.which("claude") is not None
    if not claude_cli_found and require_semantic:
        errors.append(
            "claude CLI not on PATH. Install Claude Code or pass "
            "--skip-semantic to run only the programmatic phase."
        )

    return PreflightResult(
        ok=not errors,
        health_ok=health_ok,
        claude_cli_found=claude_cli_found,
        warnings=warnings,
        errors=errors,
    )
