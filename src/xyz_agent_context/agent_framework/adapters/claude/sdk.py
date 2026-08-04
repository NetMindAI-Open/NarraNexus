""" 
@file_name: sdk.py
@author: NetMind.AI
@date: 2025-11-15
@description: This file is the main file for the xyz claude agent sdk.
"""


import asyncio
import hashlib
import json
import subprocess
from contextlib import aclosing, suppress
from pathlib import Path

from loguru import logger
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from typing import Any, AsyncGenerator

from xyz_agent_context.agent_framework.loop.cancellation_view import (
    CancellationView,
)
from xyz_agent_context.agent_framework.loop.events import (
    DATA_TYPE_ERROR,
    ITEM_TYPE_TOOL_CALL,
    TYPE_RAW_RESPONSE_EVENT,
    TYPE_RUN_ITEM_STREAM_EVENT,
)
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.agent_framework.loop.output_transfer import output_transfer
from xyz_agent_context.agent_framework.api_config import claude_config
from xyz_agent_context.agent_framework.providers.model_catalog import resolve_cli_alias
from xyz_agent_context.agent_framework.adapters._tool_policy_guard import build_tool_policy_guard
from xyz_agent_context.agent_framework.adapters.claude.cli_binary import (
    PINNED_CLI_VERSION,
    effective_cli_version,
    resolve_cli_path,
)
from xyz_agent_context.agent_framework.llm.failure import is_credential_error
from xyz_agent_context.agent_framework.adapters.claude.transcript import (
    prepare_transcript,
    remove_transcript,
)
from xyz_agent_context.agent_framework.adapters.claude.prompts import (
    append_reply_reminder,
)
from xyz_agent_context.agent_framework.adapters.materializer import (
    assemble_argv_prompt,
    split_for_argv,
)


def _oauth_expires_at(blob: str) -> float | None:
    """Epoch-ms ``claudeAiOauth.expiresAt`` from a Claude Code credentials JSON
    blob, or None when the blob is unparseable / the field is absent.

    This is the freshness key the Keychain staging compares on. NEVER logs
    ``blob`` — it carries the OAuth access + refresh tokens.
    """
    try:
        oauth = json.loads(blob).get("claudeAiOauth")
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(oauth, dict):
        return None
    exp = oauth.get("expiresAt")
    return float(exp) if isinstance(exp, (int, float)) else None


def _read_keychain_blob() -> str | None:
    """macOS: the raw Claude Code OAuth blob from the login Keychain, or None
    when there is no entry / the read fails.

    On macOS ``claude login`` writes the OAuth token to the login Keychain
    (service ``Claude Code-credentials``), NOT to ``~/.claude/.credentials.json``
    — so this is the source of truth the user's own ``claude`` reads. Isolated
    as a seam so tests can stub it. NEVER logs the returned blob — it carries
    the OAuth access + refresh tokens.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:  # noqa: BLE001 — Keychain read is best-effort
        logger.warning(f"[ClaudeAgentSDK] macOS Keychain read failed: {e}")
        return None
    blob = (proc.stdout or "").strip()
    if proc.returncode != 0 or not blob:
        return None  # no Keychain entry (e.g. logged out)
    return blob


def _stage_blob_newest_wins(
    config_dir: str | Path, blob: str, *, sourced_from: str
) -> None:
    """Atomically stage ``blob`` as ``.credentials.json`` (0600) in the isolated
    dir, newest-wins by the token's own ``claudeAiOauth.expiresAt``.

    Used for the macOS Keychain source, whose items carry no mtime to compare —
    so we compare the credential payload instead. Re-stage ONLY when ``blob`` is
    strictly newer than the already-staged copy; this makes a fresh
    ``claude login`` propagate automatically while preserving a token the
    isolated file-mode CLI refreshed in place (its staged ``expiresAt`` is >=
    the source copy → keep it, and never re-inject an already-consumed refresh
    token, the logout #76's newest-wins avoids). An unparseable ``blob``
    (no ``expiresAt``) never clobbers a good staged file.
    """
    import os

    dest_dir = Path(config_dir)
    dest = dest_dir / ".credentials.json"
    new_exp = _oauth_expires_at(blob)

    if dest.is_file():
        try:
            staged_blob = dest.read_text(encoding="utf-8")
        except OSError:
            staged_blob = None  # unreadable staged file → re-stage from source
        if staged_blob is not None:
            staged_exp = _oauth_expires_at(staged_blob)
            # Keep the staged copy unless the source is strictly newer.
            # Unparseable source (new_exp is None) → never clobber a good file.
            if new_exp is None or (staged_exp is not None and staged_exp >= new_exp):
                return

    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".credentials.json.{os.getpid()}.tmp"
    try:
        tmp.write_text(blob, encoding="utf-8")  # NEVER log `blob` — it's the token
        with suppress(OSError):
            tmp.chmod(0o600)  # keep the credential private
        os.replace(tmp, dest)  # atomic
    finally:
        with suppress(OSError):
            tmp.unlink()
    logger.info(
        f"[ClaudeAgentSDK] staged Claude OAuth credential (source: {sourced_from}) "
        f"→ {dest} (0600)"
    )


def _stage_claude_oauth_credentials(config_dir: str | Path) -> None:
    """Copy the host Claude OAuth credential into the isolated CONFIG_DIR.

    OAuth used to point ``CLAUDE_CONFIG_DIR`` straight at ``~/.claude`` so the
    CLI could read ``~/.claude/.credentials.json``. That re-opened the very
    leak #72 closed for keyed auth: the personal ``~/.claude/settings.json``
    ``env`` block (ANTHROPIC_BASE_URL/AUTH_TOKEN) overrode our provider and
    503'd, and the agent_loop raced the user's own Claude Code on
    ``~/.claude/.claude.json`` (2026-07-09 incident). OAuth now gets its own
    isolated dir; we stage ONLY the credential file into it — never
    ``settings.json`` — mirroring Codex's ``_stage_codex_oauth_credentials``.

    ``config_dir`` is ``settings.claude_oauth_config_path`` (a ``str`` or
    ``Path``); it is the same dir ``ClaudeConfig.to_cli_env`` puts in
    ``CLAUDE_CONFIG_DIR`` for the OAuth branch.

    Source precedence — macOS vs Linux/cloud:
      * **macOS**: the login **Keychain** is Claude Code's source of truth
        (``claude login`` writes there; the user's own ``claude`` reads there).
        A ``~/.claude/.credentials.json`` on macOS is a STALE relic from an
        older CLI. The Keychain is therefore preferred WHENEVER it has an entry;
        the host file is used only as a fallback when the Keychain is empty.
        2026-07-12 incident: the old code preferred the host file whenever it
        existed, so an expired Jun-25 relic shadowed a freshly-logged-in
        Keychain token — the isolated CLI read the expired file and reported
        "Not logged in · Please run /login" on every turn.
      * **Linux/cloud**: there is no Keychain; the host file is the only source.
        Behavior there is byte-identical to #76 (copy2, newest-wins by mtime).

    newest-wins: stage a source only when it is newer than the already-staged
    copy. This propagates a fresh ``claude login`` while NOT clobbering a token
    the CLI refreshed in-place inside the isolated dir — clobbering it would
    break rotating refresh tokens (the source still carries the consumed one).
    Keychain freshness is compared by the token's ``expiresAt``; host-file
    freshness by mtime.

    KNOWN COST (host may be logged out): this stages one-way source → isolated
    dir. If the isolated CLI refreshes the OAuth token in place, the source
    keeps the now-rotated refresh token and the user's own interactive
    ``claude`` gets logged out once its access token expires. Accepted tradeoff,
    matching Codex's one-way ``_stage_codex_oauth_credentials``; no write-back.
    """
    import os
    import shutil
    import sys

    from xyz_agent_context.agent_framework.providers.driver.derive import (
        CLAUDE_CLI_CREDENTIALS_REF,
        resolve_claude_credentials_path,
    )

    source = resolve_claude_credentials_path(CLAUDE_CLI_CREDENTIALS_REF)

    if sys.platform == "darwin":
        # Keychain first — it is the source of truth on macOS and must not be
        # shadowed by a stale host-file relic. Fall through to the host file
        # only when the Keychain has no entry (e.g. a legacy CLI that wrote a
        # file instead). darwin-ONLY: on Linux/cloud there is no Keychain.
        kc_blob = _read_keychain_blob()
        if kc_blob is not None:
            _stage_blob_newest_wins(config_dir, kc_blob, sourced_from="macOS Keychain")
            return

    if source is None or not source.is_file():
        logger.warning(
            f"[ClaudeAgentSDK] OAuth credential not found at "
            f"{source or '~/.claude/.credentials.json'}; the agent_loop CLI "
            "may prompt for login or fail auth. Run 'claude login' or "
            "sign in from Settings → Providers."
        )
        return

    dest_dir = Path(config_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ".credentials.json"
    if dest.exists() and dest.stat().st_mtime >= source.stat().st_mtime:
        return  # staged copy is at least as fresh — preserve any CLI refresh
    # Atomic stage: copy2 into a same-dir temp, then os.replace (atomic rename
    # on POSIX). The OAuth config dir is SHARED across concurrent agent_loops
    # and a running CLI may be reading ``.credentials.json`` at this instant —
    # a bare copy2 onto ``dest`` truncates-then-writes, re-opening the very
    # half-read / concurrent-write window this fix set out to close. copy2
    # preserves mtime so newest-wins still holds after the rename. chmod the
    # temp BEFORE the rename so ``dest`` is never briefly world-readable.
    tmp = dest_dir / f".credentials.json.{os.getpid()}.tmp"
    try:
        shutil.copy2(source, tmp)
        with suppress(OSError):
            tmp.chmod(0o600)  # credential file: keep it private
        os.replace(tmp, dest)  # atomic
    finally:
        with suppress(OSError):
            tmp.unlink()  # no-op after a successful replace; cleans up on error


def _resolve_reasoning_options(thinking: str, reasoning_effort: str) -> dict[str, Any]:
    """Map the framework-neutral slot params to ClaudeAgentOptions kwargs.

    ⚠️ This maps to what claude-agent-sdk 0.1.43 + the Claude Code CLI
    actually accept — NOT 1:1 to the Anthropic API thinking shape.

    How the SDK/CLI handle thinking (verified 2026-06-11 against
    ``claude_agent_sdk/_internal/transport/subprocess_cli.py`` + CLI 2.1.x):
      * The SDK converts ``ClaudeAgentOptions.thinking`` into a
        ``--max-thinking-tokens N`` CLI flag (adaptive→32000, enabled→budget,
        disabled→0). It NEVER passes ``--thinking adaptive``.
      * The Claude Code CLI turns a POSITIVE ``--max-thinking-tokens`` into the
        LEGACY ``thinking:{type:"enabled",budget_tokens:N}`` API shape — which
        every current model (Opus 4.6/4.7/4.8, Sonnet 4.6, Fable 5) rejects
        with ``400 "thinking.type.enabled" is not supported for this model``.
      * The ONLY adaptive lever the CLI exposes is ``--effort <level>``. With
        ``--effort`` set and NO ``--max-thinking-tokens``, the CLI uses
        adaptive thinking at that effort. With no flags at all it falls back
        to the rejected ``enabled`` default.

    Therefore (incident 2026-06-11):
      on / auto / unknown -> {"effort": <level>}  — NO "thinking" key, so the
                             SDK omits --max-thinking-tokens and the CLI goes
                             adaptive. Auto/unknown effort defaults to "high"
                             (the Anthropic server default) so --effort is
                             ALWAYS present — with no flags the CLI sends the
                             rejected enabled shape.
      off                 -> {"thinking": {"type": "disabled"}}  — the one
                             --max-thinking-tokens value (0) that maps to a
                             non-enabled request. Effort is moot with thinking
                             off.

    We never emit a positive --max-thinking-tokens, so we never produce the
    rejected ``enabled`` shape. SlotConfig validates the vocabulary; unknown
    values degrade safely (adaptive / "high") with a warning, never raise —
    a broken tuning knob must not take the agent loop down.

    Caveat: a slot deliberately pinned to a pre-4.6 model that only accepts
    ``enabled`` + budget_tokens (e.g. Sonnet 4.5) would not get a thinking
    budget here. The platform targets current models, where effort-driven
    adaptive is the path; revisit only if we add legacy-model support.
    """
    if thinking == "off":
        # Disabled → SDK emits --max-thinking-tokens 0 → thinking off. This is
        # the only --max-thinking-tokens value the CLI does NOT turn into the
        # rejected enabled shape. No effort needed with thinking off.
        return {"thinking": {"type": "disabled"}}

    if thinking and thinking != "on":
        logger.warning(
            f"[ClaudeAgentSDK] Unknown neutral thinking value {thinking!r}; "
            f"treating as adaptive (effort-driven)"
        )

    if reasoning_effort in ("low", "medium", "high", "max"):
        effort = reasoning_effort
    else:
        if reasoning_effort:
            logger.warning(
                f"[ClaudeAgentSDK] Unknown neutral reasoning_effort value "
                f"{reasoning_effort!r}; defaulting to 'high'"
            )
        effort = "high"
    # Deliberately NO "thinking" key — see docstring. Setting it makes the SDK
    # emit --max-thinking-tokens, which the CLI turns into the rejected
    # ``enabled`` shape. --effort alone drives adaptive thinking.
    return {"effort": effort}


def _stderr_tail_detail(cli_stderr_lines: list[str] | None) -> str:
    """Return the trailing CLI stderr as a `` \\n\\nCLI stderr:\\n...`` suffix,
    or ``""`` when there is none. Shared by the zero-output and inline-error
    event builders so both fold the SAME diagnostic tail into
    ``error_message`` for downstream classification."""
    stderr = "\n".join((cli_stderr_lines or [])[-30:]).strip()
    return f"\n\nCLI stderr:\n{stderr}" if stderr else ""


def _zero_output_error_event(cli_stderr_lines: list[str]) -> dict:
    """Build a ``response.error`` event for a run where the Claude CLI
    yielded zero messages.

    Zero messages means the run silently produced nothing — the CLI is not
    logged in / the OAuth session expired / it crashed / quota is exhausted.
    Emitting this event (instead of only logging and letting the generator
    end quietly) is what stops the downstream helper-LLM from fabricating a
    hollow reply over a turn that never ran (the "mysterious fallback" the
    Owner reported). Classification stays in response_processor: the raw CLI
    stderr rides along in ``error_message`` so ``_is_auth_failure`` turns an
    auth/login stderr into a fatal ``AUTH_EXPIRED`` (re-login prompt, no_reply
    fallback skipped) while a non-auth crash stays a recoverable "no output"
    error. The base sentence is kept auth-phrase-free on purpose so it can't
    false-positive the classifier when stderr is empty.
    """
    return {
        "type": TYPE_RAW_RESPONSE_EVENT,
        "data": {
            "type": DATA_TYPE_ERROR,
            "error_type": "no_output",
            "error_message": (
                "The coding agent produced no output (0 messages)."
                + _stderr_tail_detail(cli_stderr_lines)
            ),
        },
    }


def _assistant_error_text(message: Any) -> str:
    """The assistant message's own text, which on some failures IS the cause.

    When the CLI cannot reach the model it answers *in band* — a normal
    ``AssistantMessage`` whose only TextBlock reads ``API Error: 400 {...}`` —
    and writes nothing to stderr. That body is the most specific description of
    the failure anyone has, so it must not be dropped.
    """
    parts: list[str] = []
    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _inline_assistant_error_event(
    message_error: Any,
    cli_stderr_lines: list[str] | None,
    assistant_text: str = "",
) -> dict:
    """Build a ``response.error`` event for an inline ``AssistantMessage.error``,
    folding the best available detail into ``error_message``.

    ``AssistantMessage.error`` is a 6-value enum (auth/billing/rate_limit/
    invalid_request/server_error/unknown). The real provider cause — e.g.
    ``litellm.ContextWindowExceededError: inputs 75307 > 32769`` — is collapsed
    to that enum by the CLI. Left alone, output_transfer emits just
    ``Claude API error: unknown`` and the truth is lost (the "black box" P1).
    We keep ``error_type`` = the enum (so the classifier can still key on it)
    and append whatever detail we have, so ``classify_self_serviceable`` can
    recognise a context-window / balance / model error from the message text.

    Two detail channels, tried in that order:

    - **CLI stderr** — richer when present (litellm's token counts live here).
    - **The assistant text itself** — the 2026-07-28 case: an agent pinned to a
      zero-balance NetMind account got ``API Error: 400 {"detail":"balance not
      enough..."}`` as assistant text with stderr completely empty. Before this
      fallback the user was shown the bare enum while the reason sat in a log
      line, and "Claude API error: unknown" is exactly the black box this
      function exists to prevent.

    With neither channel populated the plain enum sentence stands.
    """
    enum = str(message_error)
    detail = _stderr_tail_detail(cli_stderr_lines)
    if not detail and assistant_text.strip():
        detail = f"\n\nProvider response:\n{assistant_text.strip()}"
    return {
        "type": TYPE_RAW_RESPONSE_EVENT,
        "data": {
            "type": DATA_TYPE_ERROR,
            "error_type": enum,
            "error_message": f"Claude API error: {enum}" + detail,
        },
    }


async def _probe_provider_reachable(base_url: str | None, timeout_seconds: float) -> bool | None:
    """#7 diagnostic: is the LLM provider endpoint reachable right now?

    Fires a cheap out-of-band request to ``base_url`` (independent of the
    in-flight streaming request) so a prolonged silence can be classified:
      - True  → endpoint answered (even a 4xx) → it's up; the model is most
                likely just thinking. Do NOT interrupt (铁律 #14/#15).
      - False → connection refused / timeout / DNS error → the connection is
                most likely dead; the per-request API_TIMEOUT_MS + CLI retry
                will recover or surface it at the transport layer.
      - None  → couldn't determine (no base_url, or httpx unavailable).

    Purely diagnostic — never used to force-stop a run.
    """
    if not base_url:
        return None
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            # Any HTTP status (incl. 401/404) means the endpoint is up.
            await client.get(base_url)
        return True
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.PoolTimeout):
        return False
    except httpx.HTTPError:
        # Got far enough to produce an HTTP-layer response/redirect → reachable.
        return True
    except Exception:  # noqa: BLE001
        return None


async def _drain_stderr_after_failure(
    cli_stderr_lines: list[str], ticks: int = 4, tick_seconds: float = 0.05
) -> None:
    """Give the SDK's background stderr pump a brief window to deliver
    buffered CLI stderr before teardown cancels it.

    On a fast CLI crash (e.g. stale ``--resume`` exits 1 in ~450ms) the
    ProcessError surfaces off the stdout EOF while the diagnostic line is
    still sitting in the stderr pipe; ``transport.close()`` then cancels
    the pump task and the line is lost — which blinded both the R3
    stale-resume predicate and the error logs (2026-07-25 live incident).
    Bounded at ``ticks × tick_seconds`` (~200ms default), error path only —
    never a run cap (铁律 #14-safe).
    """
    for _ in range(ticks):
        if cli_stderr_lines:
            return
        await asyncio.sleep(tick_seconds)


# Upper bound for the CLI's own graceful exit after stdin close (natural
# completion path). This is shutdown housekeeping AFTER the response fully
# arrived — not an agent-loop cap (铁律 #14-safe). On timeout we fall back
# to today's SIGTERM teardown.
_GRACEFUL_CLI_EXIT_SECONDS = 10.0

# Separate, much smaller bound for the stdin close itself. The vendored SDK's
# ``end_input()`` acquires ``transport._write_lock``; if a concurrent write is
# stuck holding it, the await never returns and ``suppress(Exception)`` does
# NOTHING for a hang — the whole turn's generator would stall past the
# graceful-exit ceiling it was supposed to respect. Closing stdin is a
# microsecond-scale syscall in the healthy case, so 2s is already pure slack;
# on timeout we simply fall through to the SIGTERM/SIGKILL teardown.
_GRACEFUL_END_INPUT_SECONDS = 2.0


async def _graceful_cli_shutdown(
    client: Any, cancellation: Any | None = None
) -> None:
    """Let the CLI exit ON ITS OWN before transport teardown SIGTERMs it.

    ``transport.close()`` (inside ``client.disconnect()``) closes stdin and
    then IMMEDIATELY SIGTERMs the subprocess. The Claude Code CLI writes its
    session transcript (the user/assistant records ``--resume`` replays)
    through buffered writers that flush lazily / on clean exit — a SIGTERM
    right after the last ResultMessage races that flush. Live evidence
    (2026-07-25): a cold run's session JSONL held ONLY queue-operation +
    file-history-snapshot records (zero conversation records), so the next
    turn's ``--resume`` died with "No conversation found"; even healthy
    R1-era transcripts were missing their FINAL assistant record to the
    same race.

    Protocol: close stdin (``end_input``) → the CLI finishes housekeeping
    (transcript + debug-log flush) and exits 0 → bounded wait for that
    exit. ``transport.close()`` afterwards sees ``returncode`` set and
    skips the SIGTERM entirely. Best-effort and never raises; on timeout
    the finally's bounded-SIGTERM/SIGKILL teardown applies exactly as
    before. Callers must SKIP this on cancellation — a cancelled run keeps
    today's synchronous teardown.

    Every await here is bounded, and the wait ALSO races ``cancellation``:
    the caller's gate is checked once, so a Stop pressed a millisecond later
    would otherwise buy the user up to ``_GRACEFUL_CLI_EXIT_SECONDS`` of
    lingering. Racing cancellation makes "cancel during graceful shutdown"
    behave like "cancel before it" (straight to the fast teardown) while a
    NORMAL completion still waits for the CLI's clean exit — that wait is the
    transcript flush, and losing it is the 2026-07-25 regression.

    Reaches into SDK private attrs (``_transport`` / ``_process``) — the
    same deliberate tradeoff as the stall probe and the SIGKILL disconnect
    fallback (re-verified necessary on SDK 0.1.43: ``close()`` has no
    graceful-exit window).
    """
    transport = getattr(client, "_transport", None)
    if transport is None:
        return
    # Bounded: end_input() takes the transport write lock, so a stuck
    # concurrent write turns this into an unbounded hang (suppress() cannot
    # help). wait_for cancels the inner await on timeout, releasing us to the
    # SIGTERM/SIGKILL path below.
    try:
        await asyncio.wait_for(
            transport.end_input(), timeout=_GRACEFUL_END_INPUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"[ClaudeAgentSDK] stdin close (end_input) did not complete within "
            f"{_GRACEFUL_END_INPUT_SECONDS}s (write lock likely held); falling "
            f"back to transport teardown"
        )
    except Exception as e:  # noqa: BLE001 — graceful path is best-effort
        logger.debug(f"[ClaudeAgentSDK] end_input failed (ignored): {e}")

    process = getattr(transport, "_process", None)
    if process is None or process.returncode is not None:
        return

    # Race the CLI's own exit against the cancellation token, mirroring the
    # receive loop's pattern above.
    wait_task = asyncio.create_task(process.wait())
    cancel_task: asyncio.Task | None = None
    if cancellation is not None and callable(
        getattr(cancellation, "await_cancelled", None)
    ):
        cancel_task = asyncio.create_task(cancellation.await_cancelled())
    waiters: list[asyncio.Task] = [wait_task]
    if cancel_task is not None:
        waiters.append(cancel_task)
    try:
        done, _pending = await asyncio.wait(
            waiters,
            return_when=asyncio.FIRST_COMPLETED,
            timeout=_GRACEFUL_CLI_EXIT_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 — graceful path is best-effort
        logger.warning(f"[ClaudeAgentSDK] graceful CLI shutdown failed: {e}")
        done = set()
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()

    if wait_task in done:
        # Consume the result/exception so asyncio doesn't warn about it.
        with suppress(Exception):
            await wait_task
        logger.debug("[ClaudeAgentSDK] CLI exited cleanly after stdin close")
        return

    wait_task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await wait_task
    if CancellationView(cancellation).requested():
        logger.info(
            "[ClaudeAgentSDK] cancellation fired during graceful CLI shutdown; "
            "skipping the remaining wait and going straight to teardown"
        )
    else:
        logger.warning(
            f"[ClaudeAgentSDK] CLI did not exit within "
            f"{_GRACEFUL_CLI_EXIT_SECONDS}s of stdin close; falling back to "
            f"transport teardown (SIGTERM)"
        )


def _is_zero_output_error_event(event: dict) -> bool:
    """True for the ``no_output`` response.error built by
    ``_zero_output_error_event`` — the zero-message failure shape the
    stale-resume retry keys on (paired with the stderr phrase check)."""
    if event.get("type") != TYPE_RAW_RESPONSE_EVENT:
        return False
    data = event.get("data") or {}
    return data.get("type") == DATA_TYPE_ERROR and data.get("error_type") == "no_output"



def _build_claude_mcp_config(
    mcp_servers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert ``{name: {"url", "headers"?}}`` specs to SDK SSE configs.

    Module-internal servers carry only ``url``; user-configured external
    servers may add ``headers`` (secret values — never log them here).

    Sorted by server name (R4c): this dict is serialized into the CLI's
    MCP config, and the request's tools array order follows the CLI's
    per-server tool enumeration. Sorting here makes OUR contribution
    byte-deterministic across turns/processes regardless of upstream
    insertion order (module servers + merged ``pass_mcp_servers``). The
    residual nondeterminism — the CLI merging tool lists from concurrently
    connected servers in completion order — is CLI-internal and outside
    our control. (The codex adapters already sort; this aligns claude.)
    """
    config: dict[str, dict[str, Any]] = {}
    for name in sorted(mcp_servers):
        spec = mcp_servers[name]
        entry: dict[str, Any] = {"type": "sse", "url": spec["url"]}
        if spec.get("headers"):
            entry["headers"] = spec["headers"]
        config[name] = entry
    return config




def _log_sysprompt_sha(system_prompt: str, resume_session_id: str | None) -> str:
    """Emit the greppable ``sys_sha256=<12hex>`` line over the COMPLETE
    adapter-facing system prompt and return the digest.

    Instrument calibration (R4c, experiment E2 2026-07-25): the hash MUST
    cover exactly the string handed to the SDK as ``options.system_prompt``
    — the CLI forwards it verbatim as the request's system[2] block.
    ContextRuntime's earlier hash missed two adapter-added byte sources:
    the per-system-message "\\n" joins in agent_loop's message split, and
    the cold-round ``=== Chat History ===`` tail appended by
    ``assemble_argv_prompt``. So the canonical ``sys_sha256`` is emitted
    HERE, post-assembly; the [SYSPROMPT-BREAKDOWN] line in context_runtime
    now labels its narrower pre-adapter hash ``ctx_sha256``.

    Reading the sentinel: two consecutive resume rounds with a byte-stable
    prompt log the SAME value. The FIRST resume round after a
    history-carrying cold round logs a DIFFERENT value than that cold round
    — expected and bounded (the cold round's history tail is absent on
    resume; one full cache write, by design).
    """
    digest = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]
    logger.info(
        f"[SYSPROMPT-SHA] chars={len(system_prompt)} "
        f"resume={'yes' if resume_session_id else 'cold'} "
        f"sys_sha256={digest}"
    )
    return digest


class ClaudeAgentSDK:
    def __init__(self, working_path: str = "./"):
        self.working_path = working_path
    
    def capabilities(self) -> set[str]:
        """Base contract only — nothing beyond what step_3 already uses.

        See ``AgentLoopDriver.capabilities`` for the negotiation seam and
        the planned vocabulary. Declare a capability here only in the
        same change that actually implements it.
        """
        return set()

    # TODO: Input is not ideal; should use a pydantic model for validation. Store it in src/xyz_agent_context/agent_framework/schema.py.
    @timed("llm.claude.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,  # Whether to use streaming output
        extra_env: dict[str, str] | None = None,  # Additional env vars (e.g., skill-configured API keys)
        cancellation: Any | None = None,  # CancellationToken for cooperative cancellation
        **kwargs: Any,
        ) -> AsyncGenerator[dict[str, Any], None]:

        # Step 0-1: Convert mcp_servers specs to the SDK's McpSSEServerConfig
        # shape. "headers" (user-configured auth, e.g. Authorization bearer
        # tokens) rides through verbatim — the SDK sends them on connect.
        claude_agent_mcp_dict = _build_claude_mcp_config(mcp_servers)
        
        # Step 0-2: Materialize the structured messages into (system
        # prompt, per-turn user message). The CLI accepts only
        # `--system-prompt <argv>` + one user input. The full strategy —
        # argv char+byte ceilings (Linux MAX_ARG_STRLEN), history budget
        # with source-aware eviction, truncation markers, and the
        # 115K-chars rationale (2026-07-03 live diagnosis) — lives in
        # adapters/materializer.py. NOTE: split_for_argv POPS the last
        # message off `messages` (load-bearing for step_3's fallback,
        # which reads the same list afterwards) — exactly once per turn.
        #
        # Where multi-turn history goes:
        #   * history present — it rides the transcript written just below, and
        #     the system prompt gets NO history block. Module instructions still
        #     pass every round; they may legally change, and a changed prompt
        #     merely forfeits that turn's cache hit (E1 T4 proved safety).
        #   * no history (a genuine first turn) — nothing to resume, so the
        #     prompt is assembled exactly as it always was.
        #
        # The session id used to arrive from upstream via **kwargs, resolved from
        # a stored handle. That whole mechanism is gone (2026-07-29): the only
        # producer is the transcript below, so a handle can no longer be stale,
        # contested between concurrent runs, or anchored to a narrative that
        # moved mid-turn.
        base_system_prompt, history_entries, this_turn_user_message = (
            split_for_argv(messages)
        )

        # Reply-surface reminder — the platform's declared delivery tools for
        # THIS turn's origin (TurnInput.expressive_tools), rendered at the end
        # of the user message. NexusPower repeats this rule per step next to
        # the generation point; the CLI's closest equivalent seam is here.
        # Rides only the live prompt input — never the transcript/history, so
        # it cannot accumulate across turns. Empty declaration (team rooms,
        # unknown surface) leaves the message untouched.
        this_turn_user_message = append_reply_reminder(
            this_turn_user_message, kwargs.get("expressive_tools")
        )

        # Author the transcript ourselves rather than depending on the CLI still
        # remembering a session. This is what removes the LAST prefix cost that
        # handle-based resume left behind: a cold turn used to carry history in
        # the system prompt while a resume turn did not, so the two prompts
        # differed and the first resume turn after any cold turn missed from
        # `system` onward (~49K full-price tokens, measured). Writing it every
        # turn makes every turn a resume turn — the prompt is byte-identical from
        # turn one, and history rides `messages`, after the prefix.
        #
        # A FRESH id per turn (never derived from agent/narrative): the file is
        # deleted in the `finally` below, so nothing durable is left in the
        # shared CLAUDE_CONFIG_DIR for an unauthenticated /agent-loop caller to
        # read with a guessed handle. A derived, guessable id would reopen that
        # hole. T0 measured that the transcript's envelope fields — sessionId
        # included — never reach the request, so varying the id per turn costs no
        # cache.
        resume_session_id, transcript_file = prepare_transcript(
            history_entries,
            config_dir=claude_config.cli_config_dir,
            working_path=str(self.working_path),
            # The version that will actually run, not the pin: the resolver falls
            # back to the SDK's bundled binary on a mismatch, and the records'
            # `version` field must describe the real writer.
            cli_version=effective_cli_version() or PINNED_CLI_VERSION,
        )

        # Keep the bare (history-free) prompt + entries in locals: the
        # stale-handle cold retry below re-assembles the full prompt WITH
        # the preserved history_entries. The argv ceilings inside
        # assemble_argv_prompt apply on BOTH paths (a resume-turn system
        # prompt can overrun argv on its own — belt-and-braces).
        system_prompt = assemble_argv_prompt(
            base_system_prompt, [] if resume_session_id else history_entries
        )
        # Cache sentinel over the exact bytes sent as options.system_prompt (R4c).
        _log_sysprompt_sha(system_prompt, resume_session_id)

        logger.debug(f"System prompt length: {len(system_prompt):,} chars")
        logger.debug(f"Your MCP: {claude_agent_mcp_dict}")
        # "Native Claude" keeps tool_search on auto (deferred tool loading);
        # non-Claude models force it off (see below). Both subscription
        # transports (host-CLI oauth and setup-token oauth_token) are always
        # native Claude — and their model is a CLI family alias (opus/sonnet/
        # haiku), which doesn't start with "claude-", so key off auth_type too.
        _model = (claude_config.model or "")
        _is_claude_native = (
            claude_config.auth_type in ("oauth", "oauth_token")
            or _model.startswith("claude-")
            or _model in ("opus", "sonnet", "haiku")
        )
        # Framework-neutral reasoning params -> Claude dialect. Per rule #15
        # we pass whatever the user configured even on non-Claude proxies
        # (they ignore what they don't understand); the log line below makes
        # the decision greppable after the fact.
        reasoning_options = _resolve_reasoning_options(
            getattr(claude_config, "thinking", ""),
            getattr(claude_config, "reasoning_effort", ""),
        )
        logger.info(
            f"[ClaudeAgentSDK] Provider config: "
            f"model={claude_config.model or '(default)'}, "
            f"base_url={claude_config.base_url or '(official)'}, "
            f"auth_type={claude_config.auth_type}, "
            f"tool_search={'auto' if _is_claude_native else 'disabled (non-Claude model)'}, "
            f"thinking={getattr(claude_config, 'thinking', '') or 'auto'}, "
            f"effort={getattr(claude_config, 'reasoning_effort', '') or 'auto'}, "
            f"resume={resume_session_id[:12] if resume_session_id else 'cold'}"
        )
        logger.trace("[FULL_SYSTEM_PROMPT]\n{}", system_prompt)
        logger.trace("[USER_PROMPT]\n{}", this_turn_user_message)

        # stderr callback: route the Claude Code CLI's error output into our log.
        # The SDK discards stderr silently by default, which makes auth failures
        # and process crashes completely invisible.
        cli_stderr_lines: list[str] = []
        def _on_cli_stderr(line: str) -> None:
            cli_stderr_lines.append(line)
            logger.warning(f"[Claude CLI stderr] {line}")

        # Step 1: Build ClaudeAgentOptions
        # Environment for the Claude CLI child process, built from api_config
        # (non-empty values only).
        cli_env: dict[str, str] = claude_config.to_cli_env()

        # OAuth runs against an isolated CLAUDE_CONFIG_DIR (see to_cli_env):
        # stage the host credential file into it before the spawn, so the CLI
        # authenticates without ever reading the host's personal settings.json.
        if claude_config.auth_type == "oauth":
            _stage_claude_oauth_credentials(cli_env["CLAUDE_CONFIG_DIR"])

        # Make the CLI child reach our localhost MCP servers directly instead of
        # through a proxy. With http_proxy / https_proxy set system-wide (a VPN
        # proxy, typically), the CLI's requests to localhost:780x go through it
        # and come back 502 Bad Gateway.
        no_proxy_hosts = "localhost,127.0.0.1"
        cli_env["NO_PROXY"] = no_proxy_hosts
        cli_env["no_proxy"] = no_proxy_hosts

        # Clear CLAUDECODE so the CLI's nested-session detection does not refuse
        # to start. The backend inherits this variable whenever it is itself
        # launched from inside a Claude Code terminal.
        cli_env["CLAUDECODE"] = ""

        # Disable Claude Code's deferred tool loading for non-Claude models.
        # Context: when the tool set exceeds the CLI's char threshold, Claude
        # Code returns ``tool_reference`` blocks from its built-in ToolSearch
        # tool instead of fully-expanded schemas. Those reference blocks are a
        # Claude Sonnet-4+/Opus-4+ protocol extension. Non-Claude backends
        # (e.g. MiniMax served via NetMind's Anthropic-compatible proxy) do not
        # understand them, which surfaces as "the tool registry is not finding
        # the chat module send_message tool" in the model's thinking and the
        # session ends with no ``send_message_to_user_directly`` invocation.
        # Forcing ENABLE_TOOL_SEARCH=false pins the CLI to the non-deferred
        # (always-expanded) tool list on those sessions. Claude models keep
        # the default (auto) behavior so they still benefit from deferred
        # loading. See TODO-2026-04-22 T7 / BUG_FIX_LOG Bug 33.
        if not _is_claude_native:
            cli_env["ENABLE_TOOL_SEARCH"] = "false"

        # Inject skill-configured env vars (e.g., TAVILY_API_KEY, GOG_ACCOUNT)
        if extra_env:
            cli_env.update(extra_env)

        # Observability (#1): log the provider the subprocess will ACTUALLY use
        # — the EFFECTIVE env after every override, not just the configured
        # intent (logged above). A personal ~/.claude/settings.json env block
        # can silently redirect ANTHROPIC_BASE_URL off the configured provider;
        # this line makes such a hijack greppable (compare effective base_url
        # vs the configured provider) instead of a black box requiring manual
        # probing (2026-07-08 incident: 30+ blind probes to locate it).
        logger.info(
            f"[ClaudeAgentSDK] subprocess provider (effective): "
            f"base_url={cli_env.get('ANTHROPIC_BASE_URL') or '(official)'}, "
            f"auth={'token' if cli_env.get('ANTHROPIC_AUTH_TOKEN') else ('key' if cli_env.get('ANTHROPIC_API_KEY') else 'none')}, "
            f"config_dir={cli_env.get('CLAUDE_CONFIG_DIR')}"
        )

        # Install the tool-policy guard:
        #  • Cloud mode: Read/Glob/Grep must stay inside the per-agent
        #    workspace, and global-install Bash commands (brew, npm -g,
        #    apt, sudo, bare pip install) are blocked.
        #  • Local mode: only the always-on gates (lark-cli shell-out
        #    redirection + WebSearch fallback) apply; the user owns the
        #    host.
        #  • WebSearch is denied in both modes when the provider doesn't
        #    run Anthropic's server-side tools (e.g. NetMind / OpenRouter
        #    just hang 45s).
        # Hooks run before the permission-mode check, so they fire even under
        # bypassPermissions. See agent_framework/adapters/_tool_policy_guard.py.
        supports_server_tools = claude_config.supports_anthropic_server_tools
        policy_guard = build_tool_policy_guard(
            workspace=self.working_path,
            supports_server_tools=supports_server_tools,
        )

        # Defense-in-depth: when the provider doesn't speak the server-tool
        # protocol, also disallow WebSearch at the CLI level. Hooks cover
        # the main session but do NOT propagate into Task-spawned subagent
        # subprocesses; the CLI flag does. Without this, a subagent could
        # still call WebSearch and hang the whole run.
        disallowed_tools: list[str] = []
        if not supports_server_tools:
            disallowed_tools.append("WebSearch")

        # Setup-residency (B++): per-agent tool suppression decided upstream
        # (unbound channels keep only their bind tool). MERGE with the local
        # list — never replace it, the WebSearch guard above must survive.
        # Rides **kwargs so every AgentLoopDriver keeps one signature.
        extra_disallowed = kwargs.get("disallowed_tools")
        if extra_disallowed:
            disallowed_tools.extend(
                t for t in extra_disallowed if t not in disallowed_tools
            )

        # Build ClaudeAgentOptions; only pass model when explicitly configured
        options_kwargs: dict[str, Any] = dict(
            system_prompt=system_prompt,
            cwd=self.working_path,
            mcp_servers=claude_agent_mcp_dict,
            permission_mode="bypassPermissions",
            # None = unlimited. Never pass 0 here: the SDK transport only emits
            # --max-turns for truthy values today, so 0 happens to mean
            # "unlimited" — but if upstream ever switches to `is not None`,
            # 0 becomes a zero-turn hard cap on agent_loop (forbidden).
            max_turns=None,
            max_buffer_size=50 * 1024 * 1024,  # 50MB buffer size for large MCP responses (PDF parsing etc.)
            include_partial_messages=True,  # Enable token-level streaming via StreamEvent
            stderr=_on_cli_stderr,  # capture the CLI's error output
            env=cli_env,  # pass the API key and friends to the Claude CLI
            hooks={
                "PreToolUse": [
                    # Match the union of tools this guard cares about. The
                    # guard itself is cheap (string check + path resolve)
                    # so running it on every listed tool call is fine.
                    HookMatcher(matcher="Read|Glob|Grep|WebSearch|Bash", hooks=[policy_guard]),
                ],
            },
            disallowed_tools=disallowed_tools,
            # Resume the CLI session captured on a previous turn (upstream
            # already validated the handle: narrative / config fingerprint /
            # working path all match). None = cold start = SDK default.
            resume=resume_session_id,
        )
        # WHICH binary runs this turn. Absent this, the SDK silently uses the
        # copy bundled in its own wheel (CLI 2.1.56 for SDK 0.1.43) even when a
        # newer, pin-verified CLI sits on PATH — and 2.1.56 reshuffles the
        # request's `tools` array on every run, voiding the entire cache prefix
        # behind it (E3/E3c). None = no verified candidate, keep the bundled
        # binary; cli_binary logs the decision once per process.
        cli_path = resolve_cli_path()
        if cli_path is not None:
            options_kwargs["cli_path"] = cli_path
        if claude_config.model:
            # CLI family aliases only work on the OAuth/CLI path; raw API
            # transports 400 on them (upstream #57 → no_reply). Normalize at
            # the transport boundary — model strings are free text upstream.
            options_kwargs["model"] = resolve_cli_alias(
                claude_config.model, auth_type=claude_config.auth_type
            )
        # Neutral reasoning params (slot-configured); absent keys keep CLI
        # defaults — identical to today's behavior when unconfigured.
        options_kwargs.update(reasoning_options)
        options = ClaudeAgentOptions(**options_kwargs)


        # Step 2: Create a ClaudeSDKClient instance, send the user message, and receive the response
        # IDLE_PROBE_SECONDS is NOT a hard cap — per CLAUDE.md 铁律 #14
        # the agent_loop has no force-stop. It's just the cadence at
        # which we log a WARNING ("CLI silent for Ns, subprocess alive,
        # still waiting"), probe subprocess liveness, AND probe the
        # provider endpoint's reachability (#7 diagnostic: distinguishes
        # "model is thinking" from "connection is dead"). If the CLI
        # subprocess has actually died we surface that as an error;
        # otherwise we continue waiting indefinitely. .env-tunable via
        # LLM_STALL_PROBE_AFTER_SECONDS.
        from xyz_agent_context.settings import settings as _settings
        IDLE_PROBE_SECONDS = max(30, _settings.llm_stall_probe_after_seconds)

        async def _run_once(
            run_options: ClaudeAgentOptions,
        ) -> AsyncGenerator[dict[str, Any], None]:
            """One CLI run: connect → query → receive loop → teardown.

            Extracted (agent-loop resume R3) so the stale-resume fallback can
            drive the SAME machinery a second time — cold, with history — in
            the same turn. This is the pre-extraction Step 2 body verbatim
            (plus the natural-completion graceful shutdown and the error-path
            stderr drain); single-run behavior is unchanged.
            """
            client = None
            message_count = 0
            # `message_task` is bound inside the receive loop but referenced
            # by the outer `finally:` for cleanup — hoist its declaration
            # here so an early failure (e.g. connect() raising) does not
            # cause the finally to NameError on the cleanup access.
            message_task: asyncio.Task | None = None
            # Dedup set. With include_partial_messages=True the SAME ToolUseBlock
            # arrives on both the partial and the complete AssistantMessage, which
            # would emit the tool_call_item twice. Keyed on tool_call_id, first
            # occurrence wins.
            seen_tool_call_ids: set[str] = set()
            try:
                client = ClaudeSDKClient(options=run_options)
                logger.info("[ClaudeAgentSDK] Connecting to Claude Code CLI...")
                await client.connect()
                logger.info("[ClaudeAgentSDK] Connected. Sending query...")
                await client.query(this_turn_user_message)
                logger.info("[ClaudeAgentSDK] Query sent. Waiting for responses...")

                # Race-with-cancel receive loop.
                #
                # Previously this loop used ``asyncio.wait_for(__anext__(),
                # IDLE_TIMEOUT_SECONDS)`` and checked cancellation only after a
                # message arrived. That meant cancellation issued while a tool
                # call (e.g. a long-running Bash command) was in flight could
                # not be detected until the tool returned a message — which
                # could take tens of seconds or minutes.
                #
                # The race pattern below waits on TWO awaitables simultaneously:
                #   * the next message arriving from Claude Code CLI
                #   * the cancellation token firing
                # whichever finishes first wins, and the still-pending one is
                # cancelled. This brings the Stop-to-loop-exit latency down to
                # the time it takes a single await round-trip — sub-100 ms on
                # any realistic host — regardless of what the CLI is doing.
                response_iter = client.receive_response().__aiter__()
                # `message_task` (declared at function scope above) lives
                # ACROSS iterations so a silent-but-alive CLI does not lose
                # its in-flight `__anext__()`. The outer finally below
                # cancels it if a message is still in flight on exit.
                while True:
                    if message_task is None or message_task.done():
                        message_task = asyncio.create_task(response_iter.__anext__())
                    cancel_task: asyncio.Task | None = None
                    if cancellation is not None:
                        cancel_task = asyncio.create_task(cancellation.await_cancelled())
                    waiters: list[asyncio.Task] = [message_task]
                    if cancel_task is not None:
                        waiters.append(cancel_task)

                    try:
                        done, pending = await asyncio.wait(
                            waiters,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=IDLE_PROBE_SECONDS,
                        )
                    finally:
                        # cancel_task is per-iteration — always cancel the
                        # still-pending one. message_task lives across
                        # iterations; do NOT cancel it here.
                        if cancel_task is not None and not cancel_task.done():
                            cancel_task.cancel()

                    if CancellationView(cancellation).requested():
                        logger.info(
                            f"[ClaudeAgentSDK] Cancellation detected after "
                            f"{message_count} messages (mid-wait), stopping"
                        )
                        if not message_task.done():
                            message_task.cancel()
                        # Suppress message_task exceptions when it was the
                        # one we cancelled — silently consume so the event
                        # loop doesn't log "Task exception was never
                        # retrieved".
                        with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                            await message_task
                        message_task = None
                        break

                    if message_task not in done:
                        # IDLE_PROBE_SECONDS elapsed with no message and no
                        # cancellation. Per CLAUDE.md 铁律 #14 we do NOT
                        # force-stop agent_loop on silence — DeepSeek-V4-Pro
                        # CoT and other long-thinking models legitimately
                        # produce minutes-long silent passes. Just probe
                        # subprocess liveness and continue waiting.
                        process = (
                            getattr(getattr(client, "_transport", None), "_process", None)
                            if client is not None else None
                        )
                        cli_returncode = getattr(process, "returncode", None) if process else None
                        if process is None or cli_returncode is None:
                            # #7 diagnostic: subprocess alive but silent. Probe the
                            # provider endpoint out-of-band to tell "model thinking"
                            # (provider reachable) from "connection dead" (provider
                            # unreachable). Diagnostic ONLY — we never force-stop
                            # here (铁律 #14); the per-request API_TIMEOUT_MS + CLI
                            # retry handle a genuinely dead request at the transport
                            # layer. Surfacing this lets ops see a stuck slot.
                            reachable = await _probe_provider_reachable(
                                getattr(claude_config, "base_url", None),
                                _settings.llm_stall_probe_timeout_seconds,
                            )
                            verdict = (
                                "provider REACHABLE (model likely thinking)"
                                if reachable is True
                                else "provider UNREACHABLE (connection likely dead — "
                                "API_TIMEOUT_MS + CLI retry should recover/surface)"
                                if reachable is False
                                else "provider reachability unknown"
                            )
                            logger.warning(
                                f"[ClaudeAgentSDK] No message for {IDLE_PROBE_SECONDS}s "
                                f"({message_count} so far); CLI subprocess still alive; "
                                f"{verdict} — continuing to wait."
                            )
                            # KEEP message_task across iterations; loop
                            # re-awaits it on the next pass.
                            continue
                        # The subprocess actually exited — that is a real
                        # failure, not LLM thinking time.
                        logger.error(
                            f"[ClaudeAgentSDK] CLI subprocess exited unexpectedly "
                            f"(returncode={cli_returncode}) after {message_count} messages "
                            f"with no in-flight response. Aborting agent loop."
                        )
                        if cli_stderr_lines:
                            logger.error("[ClaudeAgentSDK] CLI stderr:\n" + "\n".join(cli_stderr_lines))
                        if not message_task.done():
                            message_task.cancel()
                        message_task = None
                        raise RuntimeError(
                            f"Claude Code CLI subprocess exited unexpectedly "
                            f"(returncode={cli_returncode})."
                        )

                    try:
                        message = message_task.result()
                    except StopAsyncIteration:
                        message_task = None
                        break
                    # message_task has yielded its message; the next loop
                    # iteration must start a fresh one.
                    message_task = None

                    message_count += 1
                    msg_type = type(message).__name__
                    if message_count <= 5 or message_count % 20 == 0:
                        logger.debug(f"[ClaudeAgentSDK] Message #{message_count}: {msg_type}")
                    # Check AssistantMessage.error (auth failure, quota exhausted, …)
                    if msg_type == "AssistantMessage" and hasattr(message, 'error') and message.error:
                        logger.error(f"[ClaudeAgentSDK] Claude API returned an error: {message.error}")
                        # Dump CLI stderr + full message repr so we can see which
                        # field the upstream rejected. Without this the 'error' is
                        # just 'invalid_request' with no way to diagnose.
                        if cli_stderr_lines:
                            logger.error(
                                "[ClaudeAgentSDK] CLI stderr (last 30 lines):\n"
                                + "\n".join(cli_stderr_lines[-30:])
                            )
                        else:
                            logger.error(
                                "[ClaudeAgentSDK] CLI stderr: empty (error came "
                                "inline via AssistantMessage, not via CLI stderr)"
                            )
                        try:
                            logger.error(
                                f"[ClaudeAgentSDK] Full message repr: {message!r}"
                            )
                        except Exception:
                            pass

                        # Surface the real provider cause. output_transfer only sees
                        # the collapsed enum and would emit a black-box "Claude API
                        # error: unknown", so we build the event here where the
                        # detail is still in hand — CLI stderr (litellm's token
                        # counts) or, failing that, the assistant text itself, which
                        # is where an unreachable-provider 400 body arrives.
                        #
                        # Taking this branch also stops output_transfer from
                        # emitting that same text as an agent_response: an upstream
                        # "API Error: 400 ..." rendered as the agent's own reply is
                        # how a billing failure ended up looking like the agent
                        # talking nonsense to the user.
                        inline_text = _assistant_error_text(message)
                        if cli_stderr_lines or inline_text:
                            yield _inline_assistant_error_event(
                                message.error, cli_stderr_lines, inline_text
                            )
                            continue

                    # output_transfer returns a LIST — one message can yield several events.
                    events = output_transfer(message, transfer_type="claude_agent_sdk", streaming=streaming)
                    for event in events:
                        # Dedup tool_call_item by tool_call_id.
                        item = event.get("item", {}) if event.get("type") == TYPE_RUN_ITEM_STREAM_EVENT else {}
                        if item.get("type") == ITEM_TYPE_TOOL_CALL:
                            tool_id = item.get("tool_call_id", "")
                            if tool_id and tool_id in seen_tool_call_ids:
                                logger.debug(f"[ClaudeAgentSDK] Skipping duplicate tool_call: {tool_id}")
                                continue
                            if tool_id:
                                seen_tool_call_ids.add(tool_id)
                        yield event

                logger.info(f"[ClaudeAgentSDK] Stream ended. Total messages received: {message_count}")
                # Natural completion (NOT user cancellation): give the CLI a
                # clean exit BEFORE the finally's bounded-SIGTERM teardown, so
                # it flushes the session transcript `--resume` needs next turn
                # (2026-07-25 regression: transcript lost ALL conversation
                # records to the immediate SIGTERM). Cancellation keeps
                # today's fast synchronous teardown — skip straight to finally.
                # The gate is a fast path only; _graceful_cli_shutdown ALSO
                # races the token internally, so a Stop pressed right after
                # this check still short-circuits to the fast teardown.
                if not CancellationView(cancellation).requested():
                    await _graceful_cli_shutdown(client, cancellation)
                if message_count == 0:
                    logger.error(
                        "[ClaudeAgentSDK] Received 0 messages. Likely causes:\n"
                        "  1. Claude Code is not logged in (run `claude` in a terminal to authenticate)\n"
                        "  2. The Claude Code CLI process crashed\n"
                        "  3. API auth failed, or the quota is exhausted"
                    )
                    if cli_stderr_lines:
                        logger.error("[ClaudeAgentSDK] CLI stderr:\n" + "\n".join(cli_stderr_lines))
                    # Surface the silent void as a real error so response_processor
                    # can classify it (auth → fatal AUTH_EXPIRED; else recoverable)
                    # instead of the pipeline treating "no messages" as "agent
                    # chose not to reply" and fabricating a hollow fallback.
                    yield _zero_output_error_event(cli_stderr_lines)
            except GeneratorExit:
                logger.warning(f"Agent loop generator was closed early (client disconnected). Messages received: {message_count}")
            except Exception as e:
                # A fast CLI exit can raise (ProcessError off stdout EOF)
                # before the SDK's stderr pump ever ran — drain it briefly so
                # the diagnostic line (e.g. "No conversation found") reaches
                # cli_stderr_lines for the log below AND for the outer
                # stale-resume predicate. Bounded ~200ms, error path only.
                await _drain_stderr_after_failure(cli_stderr_lines)
                logger.exception(f"Error in agent_loop: {e}")
                if cli_stderr_lines:
                    logger.exception("[ClaudeAgentSDK] CLI stderr:\n" + "\n".join(cli_stderr_lines))
                raise
            finally:
                # Make sure any still-pending message_task is cancelled and
                # drained before we tear the client down — otherwise asyncio
                # will log "Task exception was never retrieved" if it raises.
                if message_task is not None and not message_task.done():
                    message_task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                        await message_task
                if client is not None:
                    # Bounded disconnect with SIGKILL fallback.
                    #
                    # claude_agent_sdk's transport.close() sends SIGTERM and
                    # then ``await self._process.wait()`` WITHOUT a timeout.
                    # If the Claude CLI subprocess hangs in cleanup or
                    # ignores SIGTERM, the disconnect coroutine never returns
                    # and the entire agent_loop finally block stalls.
                    #
                    # We bound the graceful path to 5 seconds. Beyond that we
                    # reach into the SDK's transport internals to send SIGKILL
                    # directly. This is a deliberate violation of the SDK's
                    # encapsulation; it is the only reliable way to guarantee
                    # the subprocess is reaped within a finite time window
                    # when Stop is pressed.
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[ClaudeAgentSDK] disconnect() did not complete in 5s; "
                            "force-killing Claude CLI subprocess via SIGKILL"
                        )
                        transport = getattr(client, "_transport", None)
                        process = getattr(transport, "_process", None) if transport else None
                        if process is not None and process.returncode is None:
                            with suppress(Exception):
                                process.kill()
                                with suppress(Exception):
                                    await asyncio.wait_for(process.wait(), timeout=2.0)
                    except RuntimeError as e:
                        if "cancel scope" in str(e):
                            logger.debug(f"Ignoring cancel scope error during cleanup: {e}")
                        else:
                            raise
                    except Exception as e:
                        logger.warning(f"Error during client disconnect: {e}")

        # ONE try/finally around every run this turn, so the transcript can
        # never be left behind. The removal is SYNCHRONOUS, which is what makes
        # the cancellation path safe: when this generator is aclosed,
        # GeneratorExit lands on one of the yields inside and the finally runs
        # immediately instead of whenever GC finalizes it. It covers all four
        # exits — normal completion (including the two `return`s below), the
        # except clause, cancellation, and aclose on an abandoned generator.
        #
        # The try opens BEFORE the first run rather than around each one: a
        # failure between writing the file and starting the CLI would otherwise
        # strand it, and since every turn uses a fresh session id nothing would
        # ever clean it up.
        try:
            # Drive the run(s). ``aclosing`` keeps subprocess teardown
            # deterministic: when THIS generator is closed early (cancellation),
            # the in-flight _run_once is aclosed synchronously — its finally
            # (bounded disconnect + SIGKILL fallback) runs right away instead of
            # whenever GC finalizes the inner generator.
            #
            # Cold start: exactly one run — today's behavior, byte-identical.
            if not resume_session_id:
                async with aclosing(_run_once(options)) as cold_run:
                    async for event in cold_run:
                        yield event
                return

            # Resume: one run, plus AT MOST ONE same-turn cold retry when the CLI
            # refuses to resume — it died before producing ANY content.
            #
            # The retry used to additionally require the CLI stderr to carry the
            # exact phrase "No conversation found", because the handle came from
            # a previous turn and only a *stale* one was worth retrying. Now the
            # handle is a transcript WE wrote moments ago, so any refusal is our
            # bug and a cold run is always the correct answer — matching on a
            # phrase would just be a way to miss some of our own bugs. That is
            # not hypothetical: a cwd-slug bug shipped on 2026-07-29 and survived
            # only because the CLI happened to say that exact sentence.
            #
            # Still a startup fallback, not a retry loop (铁律 #14-safe): at most
            # one retry, and only before any output. A failure after content has
            # been yielded re-raises, since re-running would duplicate it.
            yielded_any = False
            resume_rejected = False
            try:
                async with aclosing(_run_once(options)) as resume_run:
                    async for event in resume_run:
                        if not yielded_any and _is_zero_output_error_event(event):
                            # Swallow the zero-output error event: the cold retry
                            # below replaces this run entirely, so downstream —
                            # and the user (铁律 #16) — must not see a failure.
                            resume_rejected = True
                            continue
                        yielded_any = True
                        yield event
            except Exception as e:
                # A resume that failed before producing anything is retried cold;
                # anything after content re-raises. Caught broadly and decided on
                # `yielded_any` rather than on the exception type, because a
                # rejected --resume dies fast (~450ms) as the SDK's ProcessError
                # (exit 1), not as our RuntimeError.
                #
                # ONE exception to that: a credential failure. It also dies before
                # any output, so a type-blind rule would retry it — and the retry
                # is guaranteed to fail the same way, costing a second CLI spawn
                # and doubling the time before the user sees the real error. The
                # retry exists to cover OUR transcript bugs; a dead credential is
                # not one, and no amount of retrying makes it one.
                if yielded_any or is_credential_error(e):
                    raise
                resume_rejected = True

            if not resume_rejected:
                return

            logger.warning(
                f"[ClaudeAgentSDK] CLI refused to resume "
                f"(resume={resume_session_id[:12]}) — retrying THIS turn cold "
                f"with full history. Check the [Claude CLI stderr] lines above: "
                f"a refusal means the transcript we wrote was not accepted."
            )

            # First-run stderr served its purpose (it is the only diagnostic for
            # WHY the resume was refused); clear it so the cold retry's own
            # diagnostics are not polluted. The stderr callback closes over this
            # same list, so clearing in place re-arms it for the retry.
            cli_stderr_lines.clear()

            # Re-assemble the prompt WITH the preserved history and run cold —
            # exactly today's cold-start behavior.
            options_kwargs["system_prompt"] = assemble_argv_prompt(
                base_system_prompt, history_entries
            )
            # The retry runs cold — re-emit the sentinel for the actually-sent bytes.
            _log_sysprompt_sha(options_kwargs["system_prompt"], None)
            options_kwargs["resume"] = None
            async with aclosing(_run_once(ClaudeAgentOptions(**options_kwargs))) as retry_run:
                async for event in retry_run:
                    yield event
        finally:
            # Single removal site. A transcript left behind in the shared
            # CLAUDE_CONFIG_DIR is the cross-tenant read path the resume HMAC
            # exists to cover, and it would grow without bound.
            remove_transcript(transcript_file)
