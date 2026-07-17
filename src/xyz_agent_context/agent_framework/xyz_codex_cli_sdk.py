"""
@file_name: xyz_codex_cli_sdk.py
@author: NetMind.AI
@date: 2026-05-29
@description: NarraNexus wrapper for OpenAI Codex CLI.

Parallel to ``xyz_claude_agent_sdk.py``. Same async-generator contract
(``agent_loop(messages, mcp_servers, extra_env, cancellation)``
yielding ``{"type": "raw_response_event" | "run_item_stream_event"}``
dicts) so :mod:`agent_runtime._agent_runtime_steps.step_3_agent_loop`
can swap one for the other based on the per-user
``user_slots.agent_framework`` choice.

Key differences from the Claude wrapper
---------------------------------------
* **No Python SDK to wrap.** OpenAI does not ship a Python SDK for
  the ``codex`` coding-agent CLI; we spawn ``codex exec --json``
  directly via ``asyncio.create_subprocess_exec``.
* **System prompt via file, not argv.** Codex reads
  ``model_instructions_file`` from a per-run ``config.toml``; there
  is no 128 KiB argv limit. The source-aware history-eviction logic
  is kept (token budget still matters) but the strict byte ceiling
  from CC is dropped.
* **MCP servers via file, not dict.** Codex declares each MCP server
  as a ``[mcp_servers.<name>]`` table in the same per-run
  ``config.toml``. The wrapper writes this file fresh each call
  inside an isolated ``$CODEX_HOME`` temp directory; because Codex
  only reads ``$CODEX_HOME/config.toml`` (never the user's literal
  ``~/.codex/config.toml`` once that env var is overridden), our
  per-run file is what Codex actually loads. NOTE: we do **not**
  pass ``--ignore-user-config`` — that flag would skip our own file
  too (it skips ``$CODEX_HOME/config.toml``, regardless of where
  $CODEX_HOME points).
* **Stdout is JSON Lines.** We line-parse the subprocess's stdout,
  routing each JSON event through
  :func:`output_transfer.output_transfer` with
  ``transfer_type="codex_cli"``. The event shape is then identical
  to what the Claude wrapper yields, so
  :class:`response_processor.ResponseProcessor` consumes both
  unchanged.
* **No PreToolUse hook.** Codex has no per-call hook API; the
  tool-policy gates are baked into ``config.toml`` ``[permissions]``
  by :mod:`_codex_permission_translator`.

What's the SAME as the CC wrapper
---------------------------------
* Race-with-cancel receive loop pattern (sub-100ms cancellation latency
  even while a tool is mid-run).
* stderr capture into a list + WARNING log per line.
* Tool-call de-dup by ``tool_call_id`` across an item.started /
  item.completed pair.
* SIGTERM → 5s grace → SIGKILL fallback on cleanup.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.utils.deployment_mode import (
    DeploymentMode,
    get_deployment_mode,
)
from xyz_agent_context.utils.logging import timed

try:
    from .api_config import codex_config
    from ._codex_env import build_codex_subprocess_env
    from .output_transfer import output_transfer
    from ._codex_config_toml_builder import build_codex_config_toml
    from ._codex_permission_translator import (
        translate_tool_policy_to_codex_permissions,
    )
    from .provider_driver.derive import resolve_codex_credentials_path
except ImportError:  # pragma: no cover — direct-script fallback
    from api_config import codex_config
    from _codex_env import build_codex_subprocess_env
    from output_transfer import output_transfer
    from _codex_config_toml_builder import build_codex_config_toml
    from _codex_permission_translator import translate_tool_policy_to_codex_permissions
    from provider_driver.derive import resolve_codex_credentials_path


# Sentinel that tells the wrapper "use the system default for this
# config field". Centralised so the empty-default check is one place.
_EMPTY = ""

# Per-iteration idle probe (mirrors xyz_claude_agent_sdk's
# IDLE_PROBE_SECONDS). NOT a hard cap — we just log a warning every
# N seconds when the CLI has been silent so ops can see whether the
# model is thinking vs the subprocess is dead.
_IDLE_PROBE_SECONDS_DEFAULT = 30


# Per-line ceiling for the StreamReader that reads codex CLI stdout.
# ``asyncio.create_subprocess_exec`` defaults to
# ``streams._DEFAULT_LIMIT = 65536`` (64 KiB); once a single NDJSON event
# line exceeds it, ``StreamReader.readline`` raises
# ``ValueError: Separator not found, and chunk exceed the limit``.
# A tool_result carrying a base64 image can easily hit 150-400 KiB
# (see the 2026-07-08 multimodal-large-file incident writeup for the
# aiohttp twin of this bug). Lift the limit to match the Claude wrapper's
# ``max_buffer_size`` so both wrappers share the same ceiling.
_STDOUT_LINE_LIMIT = 50 * 1024 * 1024


class CodexSDK:
    """Codex CLI wrapper. Same interface as :class:`ClaudeAgentSDK`.

    One instance per agent_loop call — cheap to construct, holds no
    long-lived state. The ``working_path`` is the per-agent workspace
    used as the subprocess CWD.
    """

    def __init__(self, working_path: str | Path = "./"):
        self.working_path = str(working_path)

    @timed("llm.codex.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Drive one full Codex agent loop, yielding translated events.

        Signature mirrors :meth:`ClaudeAgentSDK.agent_loop` so the
        step_3 dispatch site is symmetric. ``**kwargs`` swallows
        wrapper-specific args (e.g. ``hooks``) the CC SDK accepts but
        we can't honor — better than crashing the dispatcher.
        """
        del kwargs  # signature compatibility only — Codex has no hook API

        if not shutil.which("codex"):
            raise RuntimeError(
                "Codex CLI ('codex' binary) is not installed or not on PATH. "
                "Install via `npm install -g @openai/codex` and run `codex login` "
                "before triggering an agent that uses the codex_cli framework."
            )

        # ---- Step 1: split system prompt + history -------------------
        system_prompt, this_turn_user_message = _build_system_prompt_and_user_msg(
            messages
        )

        idle_probe_seconds = _IDLE_PROBE_SECONDS_DEFAULT
        try:
            from xyz_agent_context.settings import settings as _settings
            idle_probe_seconds = max(30, _settings.llm_stall_probe_after_seconds)
        except Exception:  # noqa: BLE001 — settings module optional in tests
            pass

        mode: DeploymentMode = get_deployment_mode()
        is_cloud = (mode == "cloud")

        # ---- Step 2: per-run $CODEX_HOME ----------------------------
        # Tempfile context manager guarantees cleanup even on
        # CancelledError / unexpected exception.
        with tempfile.TemporaryDirectory(prefix="codex_agent_") as codex_home_str:
            codex_home_path = Path(codex_home_str)

            # 2a. instructions.md (system prompt + history)
            instructions_path = codex_home_path / "instructions.md"
            instructions_path.write_text(system_prompt, encoding="utf-8")
            # Proof-of-wiring: size + path at INFO (no content). The
            # head/tail still echoes prompt text — which routinely carries
            # credentials/context — so it only goes to DEBUG.
            logger.info(
                f"[CodexSDK] system prompt → {instructions_path} "
                f"({len(system_prompt):,} chars)"
            )
            logger.debug(
                "[CodexSDK]   head: {!r}",
                system_prompt[:160].replace("\n", " ⏎ "),
            )
            logger.debug(
                "[CodexSDK]   tail: {!r}",
                system_prompt[-160:].replace("\n", " ⏎ "),
            )
            _stage_codex_oauth_credentials(codex_home_path)

            # 2b. config.toml (MCP + custom provider + permissions)
            permissions = translate_tool_policy_to_codex_permissions(
                workspace=self.working_path,
                supports_server_tools=False,  # Codex never has Anthropic server tools
                cloud_mode=is_cloud,
            )
            # Translate MCP URLs from the SSE form Claude Code expects
            # (``http://host:PORT/sse``) to the streamable-HTTP form
            # Codex CLI's MCP client requires (``http://host:PORT/mcp``).
            # The module_runner now mounts BOTH transports on the same
            # port (see ``_serve_one_mcp``) so this rewrite is the only
            # piece of glue needed.
            # Headers are not expressible in the v1 file-based config —
            # this driver is a kept-importable fallback only (see
            # agent_framework/__init__.py); the registered CodexSDKv2 path
            # handles Authorization bearer headers via bearer_token_env_var.
            _with_headers = [n for n, sp in mcp_servers.items() if sp.get("headers")]
            if _with_headers:
                logger.warning(
                    f"[CodexSDK v1] MCP headers on {_with_headers} are ignored "
                    f"by the fallback driver; use the codex_cli (v2) driver"
                )
            codex_mcp_urls = {
                name: _sse_url_to_streamable_http(spec["url"])
                for name, spec in mcp_servers.items()
            }
            config_toml = build_codex_config_toml(
                instructions_path=instructions_path,
                mcp_server_urls=codex_mcp_urls,
                config=codex_config,
                permissions=permissions,
                writable_roots=[Path(self.working_path)],
                # ``danger-full-access`` is REQUIRED for MCP to work in
                # codex exec mode (issue #16685). Under read-only /
                # workspace-write, every MCP tool call is auto-cancelled
                # because codex tries to elicit user approval and exec
                # mode has no human responder. Our actual sandboxing
                # comes from ``self.working_path`` (per-agent workspace
                # dir) plus the ``[permissions]`` table.
                sandbox_mode="danger-full-access",
            )
            (codex_home_path / "config.toml").write_text(config_toml, encoding="utf-8")
            # INFO-level proof-of-wiring: log the MCP server names +
            # transport URLs Codex will actually see. If this list is
            # empty in the log, the wrapper handed Codex an empty
            # config and the agent will have no MCP tools at all.
            _mcp_lines = [
                f"  - {name}: {url}" for name, url in codex_mcp_urls.items()
            ] or ["  (no MCP servers configured)"]
            logger.info(
                f"[CodexSDK] config.toml → {codex_home_path / 'config.toml'} "
                f"({len(config_toml):,} bytes), MCP servers:\n"
                + "\n".join(_mcp_lines)
            )

            # ---- Step 3: env vars (allowlist — NO secret passthrough) ----
            # SECURITY (incident 2026-06-17): do NOT inherit the backend's
            # full os.environ — that handed every platform secret to any
            # agent that ran `env` in its workspace. Only a minimal system
            # allowlist + CODEX_HOME / NO_PROXY / scoped CODEX_API_KEY reach
            # the subprocess. See _codex_env.build_codex_subprocess_env.
            full_env = build_codex_subprocess_env(
                cli_env=codex_config.to_cli_env(),
                codex_home=codex_home_path,
                extra_env=extra_env,
            )

            # ---- Step 4: spawn subprocess --------------------------
            # DO NOT pass ``--ignore-user-config``: that flag skips
            # ``$CODEX_HOME/config.toml`` — but $CODEX_HOME is OUR
            # per-run temp dir, and our config.toml is what declares
            # the MCP servers, custom provider, and permissions. With
            # ``--ignore-user-config`` set, Codex silently ignored all
            # of our wiring and the agent reverted to bare Bash. The
            # original concern (don't merge the user's
            # ``~/.codex/config.toml``) is already addressed by
            # overriding ``CODEX_HOME`` to the temp dir: Codex only
            # reads config from $CODEX_HOME, so the user's home one
            # is bypassed automatically.
            cmd = [
                "codex", "exec",
                "--json",                  # JSON Lines on stdout
                "--skip-git-repo-check",   # workspace may not be a git repo
                # ``danger-full-access`` — see config.toml comment above.
                # ``workspace-write`` here would override config.toml and
                # re-break MCP. The CLI flag wins, so it must match.
                "--sandbox", "danger-full-access",
                "-",                       # read prompt from stdin
            ]
            logger.info(
                f"[CodexSDK] Spawning: codex exec --json "
                f"--skip-git-repo-check --sandbox danger-full-access -  "
                f"(cwd={self.working_path}, CODEX_HOME={codex_home_path})"
            )

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.working_path,
                    env=full_env,
                    # Bump StreamReader's per-line ceiling above the
                    # asyncio 64 KiB default — see _STDOUT_LINE_LIMIT.
                    limit=_STDOUT_LINE_LIMIT,
                )
            except FileNotFoundError as e:
                raise RuntimeError(
                    "codex CLI not found at spawn time. Install: "
                    "`npm install -g @openai/codex`."
                ) from e

            # Write user message to stdin then close (signals EOF — CLI
            # waits forever otherwise).
            assert process.stdin is not None  # set above with PIPE
            process.stdin.write(this_turn_user_message.encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()

            # stderr drainer — runs in parallel so a stderr-flooding CLI
            # doesn't block on our backpressure.
            cli_stderr_lines: list[str] = []
            stderr_task = asyncio.create_task(
                _drain_stderr(process.stderr, cli_stderr_lines)
            )

            # ---- Step 5: race-with-cancel receive loop ---------------
            #
            # Mirrors xyz_claude_agent_sdk lines 393-451. We wait on two
            # awaitables simultaneously (next line from stdout vs the
            # cancellation token) so a user Stop press is acted on within
            # ~100 ms even while a long-running tool call is in flight.
            stdout_iter = _stdout_lines(process.stdout) if process.stdout else None
            line_task: asyncio.Task | None = None
            line_count = 0
            seen_tool_call_ids: set[str] = set()
            try:
                while True:
                    if stdout_iter is None:
                        break
                    if line_task is None or line_task.done():
                        line_task = asyncio.create_task(stdout_iter.__anext__())
                    cancel_task: asyncio.Task | None = None
                    if cancellation is not None:
                        cancel_task = asyncio.create_task(
                            cancellation.await_cancelled()
                        )
                    waiters: list[asyncio.Task] = [line_task]
                    if cancel_task is not None:
                        waiters.append(cancel_task)

                    try:
                        done, _pending = await asyncio.wait(
                            waiters,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=idle_probe_seconds,
                        )
                    finally:
                        if cancel_task is not None and not cancel_task.done():
                            cancel_task.cancel()

                    if cancellation is not None and cancellation.is_cancelled:
                        logger.info(
                            f"[CodexSDK] Cancellation detected after "
                            f"{line_count} lines, stopping subprocess"
                        )
                        if not line_task.done():
                            line_task.cancel()
                        with suppress(
                            asyncio.CancelledError, StopAsyncIteration, Exception
                        ):
                            await line_task
                        break

                    if line_task not in done:
                        # Idle — log a warning and continue waiting.
                        # Binding rule #14: no force-stop based on silence.
                        if process.returncode is not None:
                            logger.error(
                                f"[CodexSDK] subprocess exited "
                                f"(returncode={process.returncode}) "
                                f"after {line_count} lines with no in-flight "
                                f"output. Aborting."
                            )
                            if cli_stderr_lines:
                                logger.error(
                                    "[CodexSDK] stderr:\n"
                                    + "\n".join(cli_stderr_lines[-30:])
                                )
                            if not line_task.done():
                                line_task.cancel()
                            with suppress(
                                asyncio.CancelledError, StopAsyncIteration, Exception
                            ):
                                await line_task
                            raise RuntimeError(
                                f"Codex CLI subprocess exited unexpectedly "
                                f"(returncode={process.returncode})."
                            )
                        logger.warning(
                            f"[CodexSDK] No output for {idle_probe_seconds}s "
                            f"({line_count} lines so far); CLI subprocess still "
                            f"alive — continuing to wait."
                        )
                        continue

                    try:
                        line = line_task.result()
                    except StopAsyncIteration:
                        break

                    line_task = None
                    line_count += 1
                    line_str = line.strip()
                    if not line_str:
                        continue

                    try:
                        codex_event = json.loads(line_str)
                    except json.JSONDecodeError:
                        # Non-JSON line — Codex sometimes emits a banner
                        # or warning on stdout. Log and skip.
                        logger.debug(
                            f"[CodexSDK] non-JSON stdout line "
                            f"({len(line_str)} chars): {line_str[:200]!r}"
                        )
                        continue

                    # Per-event translation — yields 0..N normalised events.
                    translated = output_transfer(
                        codex_event,
                        transfer_type="codex_cli",
                        streaming=streaming,
                    )
                    for event in translated:
                        # Dedupe tool calls across started/completed pair.
                        # See xyz_claude_agent_sdk lines 545-557 for the
                        # same idea (Claude has partial AssistantMessage
                        # + complete that both carry the ToolUseBlock).
                        item = (
                            event.get("item", {})
                            if event.get("type") == "run_item_stream_event"
                            else {}
                        )
                        if item.get("type") == "tool_call_item":
                            tid = item.get("tool_call_id") or ""
                            if tid and tid in seen_tool_call_ids:
                                logger.debug(
                                    f"[CodexSDK] Skipping duplicate "
                                    f"tool_call: {tid}"
                                )
                                continue
                            if tid:
                                seen_tool_call_ids.add(tid)
                        yield event

                logger.info(
                    f"[CodexSDK] Stream ended. Total lines: {line_count}"
                )
                if line_count == 0:
                    logger.error(
                        "[CodexSDK] Received 0 lines of output. Likely causes:\n"
                        "  1. Codex CLI not logged in "
                        "(run `codex login` in a terminal to authenticate)\n"
                        "  2. CODEX_API_KEY is incorrect\n"
                        "  3. Wrong model_provider in config.toml"
                    )
                    if cli_stderr_lines:
                        logger.error(
                            "[CodexSDK] stderr:\n" + "\n".join(cli_stderr_lines)
                        )

            except GeneratorExit:
                logger.warning(
                    f"[CodexSDK] generator closed early "
                    f"(client disconnected). Lines received: {line_count}"
                )
            except Exception as e:
                logger.exception(f"[CodexSDK] agent_loop error: {e}")
                if cli_stderr_lines:
                    logger.error(
                        "[CodexSDK] stderr:\n" + "\n".join(cli_stderr_lines)
                    )
                raise
            finally:
                # Drain any still-pending line_task before tearing
                # down so asyncio doesn't log "Task exception was never
                # retrieved".
                if line_task is not None and not line_task.done():
                    line_task.cancel()
                    with suppress(
                        asyncio.CancelledError, StopAsyncIteration, Exception
                    ):
                        await line_task

                # SIGTERM → grace → SIGKILL fallback. Mirrors CC wrapper
                # lines 600-613.
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.send_signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[CodexSDK] subprocess did not exit within 5s "
                            "of SIGTERM; sending SIGKILL"
                        )
                        with suppress(ProcessLookupError):
                            process.kill()
                        with suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(process.wait(), timeout=2.0)

                stderr_task.cancel()
                with suppress(Exception):
                    await stderr_task


# =============================================================================
# Helpers
# =============================================================================


async def _drain_stderr(
    stream: asyncio.StreamReader | None, sink: list[str]
) -> None:
    """Drain the subprocess stderr into ``sink`` line-by-line. Each
    line is also logged at WARNING level so it's visible in real time."""
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            sink.append(text)
            logger.warning(f"[Codex CLI stderr] {text}")


async def _stdout_lines(stream: asyncio.StreamReader) -> AsyncGenerator[str, None]:
    """Async generator that yields one decoded line per stdout newline.

    ``asyncio.StreamReader.readline`` already returns one line at a
    time including the trailing ``\\n``; we decode + strip the
    terminator. EOF returns ``b""`` — we stop the iteration.
    """
    while True:
        raw = await stream.readline()
        if not raw:
            return
        yield raw.decode("utf-8", errors="replace")


def _build_system_prompt_and_user_msg(
    messages: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build the system prompt + history text and pop the last user
    message as the per-turn prompt.

    Lighter-weight than the CC wrapper's equivalent because Codex
    reads instructions from a file (no argv length limit). We still
    apply source-aware history eviction so the prompt token cost
    stays bounded.

    NOTE — kept inline rather than shared with xyz_claude_agent_sdk
    deliberately (see plan Task 7): consolidating both into a single
    helper is a follow-up. For now, the Codex variant has different
    limits and a simpler shape.
    """
    if not messages:
        return "", ""

    # Last entry is the per-turn user message — same convention as CC.
    messages = list(messages)
    this_turn_user_message = (messages.pop()).get("content", "") or ""

    system_prompt = ""
    history_entries: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            system_prompt += (msg.get("content") or "") + "\n"
        elif role in ("user", "assistant"):
            history_entries.append({
                "role": role,
                "content": msg.get("content") or "",
                "source": msg.get("_source", "chat"),
            })

    # Codex reads from file → no argv byte ceiling. Still keep a
    # generous char budget for the history so cost stays sane.
    MAX_SYSTEM_PROMPT_CHARS = 400_000
    MAX_HISTORY_CHARS = 200_000

    def _format_entry(e: dict[str, Any]) -> str:
        label = "User" if e["role"] == "user" else "Assistant"
        return f"{label}: {e['content']}"

    if history_entries:
        body = "\n\n".join(_format_entry(r) for r in history_entries)
        # Source-aware eviction: drop oldest non-chat messages first if
        # body exceeds budget. Same priority as CC wrapper.
        while len(body) > MAX_HISTORY_CHARS and history_entries:
            bg_idx = next(
                (i for i, r in enumerate(history_entries) if r["source"] != "chat"),
                None,
            )
            if bg_idx is not None:
                history_entries.pop(bg_idx)
            else:
                history_entries.pop(0)
            body = "\n\n".join(_format_entry(r) for r in history_entries)

        if history_entries:
            system_prompt += (
                f"\n\n=== Chat History ===\n{body}\n=== Chat History End ===\n"
                " These are the chat history between you and the user. "
                "This time please make the response by user input in this turn."
            )

    if len(system_prompt) > MAX_SYSTEM_PROMPT_CHARS:
        logger.warning(
            f"[CodexSDK] system prompt {len(system_prompt)} chars > "
            f"{MAX_SYSTEM_PROMPT_CHARS}; truncating"
        )
        system_prompt = system_prompt[:MAX_SYSTEM_PROMPT_CHARS] + (
            "\n\n[...truncated due to length limit...]"
        )

    return system_prompt, this_turn_user_message


def _stage_codex_oauth_credentials(codex_home_path: Path) -> None:
    """Copy host Codex OAuth credentials into the per-run CODEX_HOME."""
    if codex_config.auth_type != "oauth" or codex_config.api_key:
        return

    source_path = resolve_codex_credentials_path(codex_config.auth_ref)
    if source_path is None:
        return
    if not source_path.is_file():
        logger.warning(
            f"[CodexSDK] Codex OAuth credentials not found at {source_path}; "
            "codex exec may ask for login or fail authentication."
        )
        return
    shutil.copy2(source_path, codex_home_path / "auth.json")


def _sse_url_to_streamable_http(url: str) -> str:
    """Rewrite a NarraNexus MCP URL from the SSE path to the streamable
    HTTP path.

    Claude Code's MCP client expects ``http://host:port/sse``; Codex
    CLI's MCP client expects the streamable HTTP transport at
    ``http://host:port/mcp``. NarraNexus's module_runner mounts BOTH
    sub-apps on the same port (see ``module_runner._serve_one_mcp``)
    so the only thing that varies between SDKs is the path.

    Conservative rule: only rewrite a literal trailing ``/sse`` (or
    ``/sse/``). Anything else passes through unchanged so the wrapper
    stays correct under future URL conventions.
    """
    if url.endswith("/sse"):
        return url[:-4] + "/mcp"
    if url.endswith("/sse/"):
        return url[:-5] + "/mcp"
    return url
