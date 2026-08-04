"""
@file_name: cli_oneshot.py
@author: NarraNexus
@date: 2026-07-31
@description: Shared one-shot runner for the codex agent-loop driver.

Two consumers need "run one tool-free turn through the registered codex
driver and tell me what came back": the helper LLM (CliHelperSDK's codex
branch, which wants text + token counts) and credential verification
(CodexOAuthDriver.verify_live, which wants a verdict). Before this module
each carried its own copy of the driver construction, the per-uid
disposable cwd, and the raw_response_event parsing — the third copy was
the review trigger (PR #224 review, item 5).

What this module owns:
- The disposable per-uid cwd. It must never be the backend process cwd
  (codex derives writable_roots from it, so a prompt-injected helper
  input could otherwise touch the app tree), and on a shared host it must
  not be a directory another local user pre-created — st_uid is checked
  and a private mkdtemp is used as the fallback (same pattern as
  narra_cli_client's home dir).
- The event contract: the codex translator (output_transfer /
  codex_official) emits {"type": "raw_response_event", "data": {...}} —
  visible text as data.type=="response.text.delta", terminal usage on
  "response.done", and failures as a terminal error EVENT (not an
  exception). error_type AND error_message are both kept: codex phrases
  auth failures as error_type="unauthorized" with a message that carries
  no credential marker on its own, and #68's is_credential_error keys off
  the combined text.

What this module does NOT own: the ambient ``_codex_ctx`` CodexConfig.
Callers install their own (the helper its slot config, verification the
card under test) — which config is correct is exactly what distinguishes
the callers.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from loguru import logger

from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_DONE,
    DATA_TYPE_ERROR,
    DATA_TYPE_TEXT_DELTA,
    TYPE_RAW_RESPONSE_EVENT,
)


@dataclass(frozen=True)
class CliOneshotResult:
    """Everything a single codex one-shot reported.

    ``error`` is the CLI's terminal error event as "type: message" ("" when
    none). Token counts are 0 when the CLI reported no usage (OAuth
    subscription calls legitimately bill the subscription, not us).
    """

    text: str
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


def oneshot_cwd(label: str) -> str:
    """A disposable, per-uid working dir for a CLI spawn.

    ``label`` namespaces consumers (helper vs verify) so their artifacts
    can't collide. Ownership is verified before reuse: with exist_ok=True a
    same-named directory pre-created by another user on a shared host would
    otherwise be silently adopted. Fallback is a private mkdtemp — always
    owned, just not reused across calls.
    """
    base = os.path.join(tempfile.gettempdir(), f"narranexus-{label}-{os.getuid()}")
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
        if os.stat(base).st_uid == os.getuid():
            os.chmod(base, 0o700)  # tighten even if it pre-existed looser
            return base
    except OSError as e:
        logger.debug(f"[cli_oneshot] shared cwd unavailable ({e}); using mkdtemp")
    return tempfile.mkdtemp(prefix=f"narranexus-{label}-")


async def run_codex_cli_oneshot(
    system_prompt: str,
    user_input: str,
    *,
    working_path: str | None = None,
) -> CliOneshotResult:
    """One tool-free turn through the registered codex agent-loop driver.

    The caller must have installed the intended CodexConfig into the
    ambient ``_codex_ctx`` — the codex driver reads its model/credentials
    from there, not from arguments. ``working_path`` is REQUIRED by the
    executor seam (RemoteAgentLoopDriver) — the default is the shared
    per-uid disposable dir.
    """
    from xyz_agent_context.agent_framework import get_agent_loop_driver

    driver = get_agent_loop_driver(
        framework="codex_cli",
        working_path=working_path or oneshot_cwd("cli-oneshot"),
    )
    text_parts: list[str] = []
    in_tok = out_tok = 0
    err_msg = ""
    async for ev in driver.agent_loop(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        mcp_servers={},
    ):
        if not isinstance(ev, dict) or ev.get("type") != TYPE_RAW_RESPONSE_EVENT:
            continue
        data = ev.get("data") or {}
        dtype = data.get("type")
        if dtype == DATA_TYPE_TEXT_DELTA:
            delta = data.get("delta") or ""
            if delta:
                text_parts.append(delta)
        elif dtype == DATA_TYPE_DONE:
            usage = data.get("usage")
            if isinstance(usage, dict):
                in_tok = int(usage.get("input_tokens", in_tok) or in_tok)
                out_tok = int(usage.get("output_tokens", out_tok) or out_tok)
        elif dtype == DATA_TYPE_ERROR:
            _etype = str(data.get("error_type") or "").strip()
            _emsg = str(data.get("error_message") or "").strip()
            err_msg = ": ".join(p for p in (_etype, _emsg) if p) or "codex error"
    return CliOneshotResult(
        text="".join(text_parts),
        error=err_msg,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


__all__ = ["CliOneshotResult", "oneshot_cwd", "run_codex_cli_oneshot"]
