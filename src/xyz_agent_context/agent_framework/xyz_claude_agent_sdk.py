""" 
@file_name: xyz_claude_agent_sdk.py
@author: NetMind.AI
@date: 2025-11-15
@description: This file is the main file for the xyz claude agent sdk.
"""


import asyncio
from contextlib import suppress

from loguru import logger
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from typing import Any, AsyncGenerator

from xyz_agent_context.utils.logging import timed

# Handle both relative import (when used as module) and absolute import (when run as script)
try:
    from .output_transfer import output_transfer
    from .api_config import claude_config
    from ._tool_policy_guard import build_tool_policy_guard
except ImportError:
    from output_transfer import output_transfer
    from api_config import claude_config
    from _tool_policy_guard import build_tool_policy_guard

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


class ClaudeAgentSDK:
    def __init__(self, working_path: str = "./"):
        self.working_path = working_path
    
    # TODO: Input is not ideal; should use a pydantic model for validation. Store it in src/xyz_agent_context/agent_framework/schema.py.
    @timed("llm.claude.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_server_urls: dict[str, str],  # Corrected type annotation: should be a dict, not a list
        streaming: bool = True,  # Whether to use streaming output
        extra_env: dict[str, str] | None = None,  # Additional env vars (e.g., skill-configured API keys)
        cancellation: Any | None = None,  # CancellationToken for cooperative cancellation
        **kwargs: Any,
        ) -> AsyncGenerator[dict[str, Any], None]:

        # Step 0-1: Convert mcp_server_urls to claude_agent_mcp_dict
        claude_agent_mcp_dict = {
            mcp_server_url[0]: {"type": "sse", "url": mcp_server_url[1]} for mcp_server_url in mcp_server_urls.items()
        }
        
        # Step 0-2: Build system prompt. Currently the Claude Agent SDK does not support multi-turn conversations,
        # so we need to manually append the conversation history to the system prompt.
        # Limit the maximum length of the system prompt to avoid "Argument list too long" errors.
        #
        # The Python SDK (see claude_agent_sdk/_internal/transport/subprocess_cli.py)
        # passes system_prompt via `--system-prompt <str>` argv. Linux limits a
        # single argv entry to MAX_ARG_STRLEN = PAGE_SIZE * 32 = 128 KiB on
        # typical x86_64 kernels. A naive char-count limit is unsafe when the
        # prompt contains multi-byte (e.g. Chinese) content — 1 char can be 3
        # UTF-8 bytes — so we apply two limits: a char-count ceiling (for
        # readability and predictability) and a byte-count ceiling (hard
        # enforcement against E2BIG).
        #
        # History: agents often run 10+ turns; 50K keeps 3-5 full turns.
        # System prompt: T8 (ENABLE_TOOL_SEARCH=false for non-Claude models)
        # forces the full MCP tool schemas (~40 tools) into the base prompt,
        # typically 60-80K chars; 100K gives headroom without hitting the
        # 128 KiB argv byte ceiling for mixed-language content.
        MAX_SYSTEM_PROMPT_LENGTH = 100_000  # chars
        MAX_SYSTEM_PROMPT_BYTES = 120 * 1024  # ~120 KiB, leaves 8 KiB for argv overhead
        MAX_HISTORY_LENGTH = 50_000  # chars

        # Source-aware history truncation (2026-05-19):
        # The earlier scheme was "append history to system_prompt, then
        # [:100_000] the whole string" — which would chop the TAIL of the
        # system instructions when history was long, breaking Module-injected
        # context. New scheme:
        #   1. Build system_prompt and the history list separately.
        #   2. Reserve the system prompt's full length within the char ceiling.
        #   3. Within the remaining budget, drop the OLDEST background-trigger
        #      messages first (`_source` in {job, message_bus, lark, callback}),
        #      then the oldest chat messages, until what's left fits.
        # `_source` is set by context_runtime.build_input_for_framework from
        # each row's `meta_data.working_source`; unknown rows default to "chat".
        # Rows are NOT deleted from the database — this only governs which
        # rows are sent to the LLM for this turn.
        system_prompt = ""
        history_entries: list[dict[str, Any]] = []  # ordered oldest -> newest
        this_turn_user_message = (messages.pop())["content"]    # TODO: Not robust enough; if the last message is not a user message, a logic error will occur. Needs adjustment.
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                system_prompt += msg["content"] + "\n"
            elif role in ("user", "assistant"):
                history_entries.append({
                    "role": role,
                    "content": msg.get("content", ""),
                    "source": msg.get("_source", "chat"),
                })

        def _format_entry(e: dict[str, Any]) -> str:
            label = "User" if e["role"] == "user" else "Assistant"
            return f"{label}: {e['content']}"

        # Char budget reserved for history within MAX_SYSTEM_PROMPT_LENGTH.
        # If system_prompt alone is already near/over the ceiling we send NO
        # history — protecting the system instructions is the priority.
        HISTORY_HEADER = "\n\n=== Chat History ===\n"
        HISTORY_FOOTER = (
            "\n=== Chat History End ===\n"
            " These are the chat history between you and the user. "
            "This time please make the response by user input in this turn."
        )
        overhead = len(HISTORY_HEADER) + len(HISTORY_FOOTER)
        sys_len = len(system_prompt)
        history_budget = max(
            0,
            min(MAX_HISTORY_LENGTH, MAX_SYSTEM_PROMPT_LENGTH - sys_len - overhead),
        )

        kept: list[dict[str, Any]] = []
        if history_entries and history_budget > 0:
            kept = list(history_entries)

            def _join_len(rows: list[dict[str, Any]]) -> int:
                if not rows:
                    return 0
                # +2 per separator "\n\n" between rows
                return sum(len(_format_entry(r)) for r in rows) + 2 * (len(rows) - 1)

            dropped_bg = 0
            dropped_chat = 0
            while kept and _join_len(kept) > history_budget:
                # Tier 1: drop the oldest non-chat row.
                bg_idx = next(
                    (i for i, r in enumerate(kept) if r["source"] != "chat"),
                    None,
                )
                if bg_idx is not None:
                    kept.pop(bg_idx)
                    dropped_bg += 1
                else:
                    # Tier 2: drop the oldest chat row.
                    kept.pop(0)
                    dropped_chat += 1

            if dropped_bg or dropped_chat:
                logger.warning(
                    f"History truncated by source-aware eviction: "
                    f"dropped {dropped_bg} background-trigger rows "
                    f"+ {dropped_chat} chat rows, kept {len(kept)} of "
                    f"{len(history_entries)} (budget {history_budget} chars)."
                )
        elif history_entries:
            logger.warning(
                f"System prompt alone ({sys_len} chars) leaves no room for "
                f"history; omitting all {len(history_entries)} history rows."
            )

        if kept:
            body = "\n\n".join(_format_entry(r) for r in kept)
            label_tag = (
                "Chat History"
                if len(kept) == len(history_entries)
                else "Chat History (truncated by source-aware eviction)"
            )
            system_prompt += (
                f"\n\n=== {label_tag} ===\n{body}{HISTORY_FOOTER}"
            )

        # Belt-and-braces (rare now): char + byte caps still apply because
        # multi-byte content blows past char budget in the worst case, and
        # the system_prompt itself might exceed MAX_SYSTEM_PROMPT_LENGTH
        # (in which case the eviction loop already gave us 0-budget history,
        # but the system prompt still needs to fit argv).
        if len(system_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            logger.warning(
                f"System prompt still too long after source-aware eviction "
                f"({len(system_prompt)} chars > {MAX_SYSTEM_PROMPT_LENGTH}), "
                f"hard-truncating to char ceiling"
            )
            system_prompt = system_prompt[:MAX_SYSTEM_PROMPT_LENGTH] + "\n\n[...truncated due to length limit...]"

        _encoded = system_prompt.encode("utf-8")
        if len(_encoded) > MAX_SYSTEM_PROMPT_BYTES:
            logger.warning(
                f"System prompt exceeds byte ceiling "
                f"({len(_encoded)} bytes > {MAX_SYSTEM_PROMPT_BYTES}), "
                f"truncating at UTF-8 boundary"
            )
            # decode('utf-8', errors='ignore') drops any partial multi-byte
            # sequence introduced by the byte slice, so the result is always
            # valid UTF-8.
            system_prompt = _encoded[:MAX_SYSTEM_PROMPT_BYTES].decode("utf-8", errors="ignore")
            system_prompt += "\n\n[...truncated due to byte limit...]"
                
        logger.debug(f"System prompt length: {len(system_prompt):,} chars")
        logger.debug(f"Your MCP: {claude_agent_mcp_dict}")
        # "Native Claude" keeps tool_search on auto (deferred tool loading);
        # non-Claude models force it off (see below). Claude Code OAuth is always
        # native Claude — and its model is now a CLI family alias (opus/sonnet/
        # haiku), which doesn't start with "claude-", so key off auth_type too.
        _model = (claude_config.model or "")
        _is_claude_native = (
            claude_config.auth_type == "oauth"
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
            f"effort={getattr(claude_config, 'reasoning_effort', '') or 'auto'}"
        )
        logger.trace("[FULL_SYSTEM_PROMPT]\n{}", system_prompt)
        logger.trace("[USER_PROMPT]\n{}", this_turn_user_message)

        # stderr 回调：将 Claude Code CLI 的错误输出记录到日志
        # SDK 默认会静默丢弃 stderr，导致认证失败、进程崩溃等问题完全不可见
        cli_stderr_lines: list[str] = []
        def _on_cli_stderr(line: str) -> None:
            cli_stderr_lines.append(line)
            logger.warning(f"[Claude CLI stderr] {line}")

        # Step 1: Build ClaudeAgentOptions
        # 从 api_config 构建传给 Claude CLI 子进程的环境变量（仅包含非空值）
        cli_env: dict[str, str] = claude_config.to_cli_env()

        # 确保 CLI 子进程绕过代理直连 localhost 的 MCP 服务器。
        # 系统若设置了 http_proxy / https_proxy（如 VPN 代理），会导致
        # Claude Code CLI 访问 localhost:780x 时走代理返回 502 Bad Gateway。
        no_proxy_hosts = "localhost,127.0.0.1"
        cli_env["NO_PROXY"] = no_proxy_hosts
        cli_env["no_proxy"] = no_proxy_hosts

        # 清除 CLAUDECODE 环境变量，避免嵌套会话检测导致子进程拒绝启动。
        # 当后端从 Claude Code 终端内启动时，子进程会继承此变量。
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
        # bypassPermissions. See agent_framework/_tool_policy_guard.py.
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
            stderr=_on_cli_stderr,  # 捕获 CLI 错误输出
            env=cli_env,  # 传递 Anthropic API Key 等环境变量给 Claude CLI
            hooks={
                "PreToolUse": [
                    # Match the union of tools this guard cares about. The
                    # guard itself is cheap (string check + path resolve)
                    # so running it on every listed tool call is fine.
                    HookMatcher(matcher="Read|Glob|Grep|WebSearch|Bash", hooks=[policy_guard]),
                ],
            },
            disallowed_tools=disallowed_tools,
        )
        if claude_config.model:
            options_kwargs["model"] = claude_config.model
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

        client = None
        message_count = 0
        # `message_task` is bound inside the receive loop but referenced
        # by the outer `finally:` for cleanup — hoist its declaration
        # here so an early failure (e.g. connect() raising) does not
        # cause the finally to NameError on the cleanup access.
        message_task: asyncio.Task | None = None
        # 去重集合：include_partial_messages=True 时，partial AssistantMessage
        # 和 complete AssistantMessage 都会携带同一个 ToolUseBlock，导致重复
        # 的 tool_call_item。通过 tool_call_id 去重，只保留首次出现。
        seen_tool_call_ids: set[str] = set()
        try:
            client = ClaudeSDKClient(options=options)
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

                if cancellation is not None and cancellation.is_cancelled:
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
                # 检测 AssistantMessage 的 error 字段（认证失败、额度不足等）
                if msg_type == "AssistantMessage" and hasattr(message, 'error') and message.error:
                    logger.error(f"[ClaudeAgentSDK] Claude API 返回错误: {message.error}")
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

                # output_transfer 返回事件列表（一条消息可能产生多个事件）
                events = output_transfer(message, transfer_type="claude_agent_sdk", streaming=streaming)
                for event in events:
                    # 对 tool_call_item 按 tool_call_id 去重
                    item = event.get("item", {}) if event.get("type") == "run_item_stream_event" else {}
                    if item.get("type") == "tool_call_item":
                        tool_id = item.get("tool_call_id", "")
                        if tool_id and tool_id in seen_tool_call_ids:
                            logger.debug(f"[ClaudeAgentSDK] Skipping duplicate tool_call: {tool_id}")
                            continue
                        if tool_id:
                            seen_tool_call_ids.add(tool_id)
                    yield event

            logger.info(f"[ClaudeAgentSDK] Stream ended. Total messages received: {message_count}")
            if message_count == 0:
                logger.error(
                    "[ClaudeAgentSDK] ⚠️ 收到 0 条消息！可能原因：\n"
                    "  1. Claude Code 未登录（终端运行 `claude` 完成认证）\n"
                    "  2. Claude Code CLI 进程崩溃\n"
                    "  3. API 认证失败或额度耗尽"
                )
                if cli_stderr_lines:
                    logger.error("[ClaudeAgentSDK] CLI stderr 输出:\n" + "\n".join(cli_stderr_lines))
        except GeneratorExit:
            logger.warning(f"Agent loop generator was closed early (client disconnected). Messages received: {message_count}")
        except Exception as e:
            logger.exception(f"Error in agent_loop: {e}")
            if cli_stderr_lines:
                logger.exception("[ClaudeAgentSDK] CLI stderr 输出:\n" + "\n".join(cli_stderr_lines))
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

