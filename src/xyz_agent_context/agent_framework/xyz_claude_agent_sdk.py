""" 
@file_name: xyz_claude_agent_sdk.py
@author: NetMind.AI
@date: 2025-11-15
@description: This file is the main file for the xyz claude agent sdk.
"""


import asyncio

from loguru import logger
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal import message_parser as _message_parser_module
from claude_agent_sdk.types import SystemMessage
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

# Monkey-patch claude_agent_sdk's parse_message to handle unknown message types gracefully.
# The SDK v0.1.6 raises MessageParseError for unrecognized types like "rate_limit_event",
# which crashes the entire agent loop. This patch converts them to SystemMessage instead.
_original_parse_message = _message_parser_module.parse_message


def _safe_parse_message(data: dict[str, Any]) -> Any:
    try:
        return _original_parse_message(data)
    except MessageParseError as e:
        if "Unknown message type" in str(e):
            msg_type = data.get("type", "unknown") if isinstance(data, dict) else "unknown"
            logger.debug(f"Skipping unrecognized message type from Claude API: {msg_type}")
            return SystemMessage(subtype=f"unknown_{msg_type}", data=data)
        raise


_message_parser_module.parse_message = _safe_parse_message


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

        system_prompt = ""
        for msg in messages:
            if msg["role"] == "system":
                system_prompt += msg["content"] + "\n"
        conversation_history = []
        user_messages = []
        this_turn_user_message = (messages.pop())["content"]    # TODO: Not robust enough; if the last message is not a user message, a logic error will occur. Needs adjustment.
        for i, msg in enumerate(messages):
            if msg["role"] == "user":
                user_messages.append(i)
                conversation_history.append(f"User: {msg['content']}")
            elif msg["role"] == "assistant":
                conversation_history.append(f"Assistant: {msg['content']}")
        # If there is conversation history, append it to the system prompt
        if len(user_messages) > 1:  # More than 1 user message indicates there is history
            history_text = "\n\n=== Chat History ===\n" + "\n\n".join(conversation_history)

            # If the history is too long, truncate and keep the most recent part
            if len(history_text) > MAX_HISTORY_LENGTH:
                logger.warning(f"Chat history too long ({len(history_text)} chars), truncating to {MAX_HISTORY_LENGTH} chars")
                # Keep the most recent history
                truncated_history = history_text[-MAX_HISTORY_LENGTH:]
                # Find the start of the first complete message
                first_user_idx = truncated_history.find("\nUser: ")
                first_assistant_idx = truncated_history.find("\nAssistant: ")
                if first_user_idx > 0 and (first_assistant_idx < 0 or first_user_idx < first_assistant_idx):
                    truncated_history = truncated_history[first_user_idx:]
                elif first_assistant_idx > 0:
                    truncated_history = truncated_history[first_assistant_idx:]
                history_text = "\n\n=== Chat History (truncated) ===\n" + truncated_history

            system_prompt += history_text
            system_prompt += "\n=== Chat History End ===\n These are the chat history between you and the user. This time please make the response by user input in this turn."

        # Final check on the total length of system_prompt — two-pass bound.
        #   Pass 1: char count. Caps human-readable size.
        #   Pass 2: UTF-8 byte count. Hard guard against the Linux 128 KiB
        #           argv limit when the prompt contains multi-byte content.
        if len(system_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            logger.warning(
                f"System prompt too long ({len(system_prompt)} chars), "
                f"truncating to {MAX_SYSTEM_PROMPT_LENGTH} chars"
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
        _is_claude_native = (claude_config.model or "").startswith("claude-")
        logger.info(
            f"[ClaudeAgentSDK] Provider config: "
            f"model={claude_config.model or '(default)'}, "
            f"base_url={claude_config.base_url or '(official)'}, "
            f"auth_type={claude_config.auth_type}, "
            f"tool_search={'auto' if _is_claude_native else 'disabled (non-Claude model)'}"
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
            max_turns=0,  # 0 = unlimited turns
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
        options = ClaudeAgentOptions(**options_kwargs)


        # Step 2: Create a ClaudeSDKClient instance, send the user message, and receive the response
        # Idle timeout: if no message is received within this duration, assume CLI is stuck.
        # Bug 20 (2026-04-20): lowered from 1200s → 600s. Every MCP tool handler now
        # self-caps at ≤60s via `with_mcp_timeout` (see common_tools_module), and
        # Claude CLI built-in tools (WebFetch/Bash) have their own short internal
        # timeouts. 1200s was "20 minutes of complete silence" — that length of
        # idle means something deeper is broken; 10 minutes gives reasonable
        # margin for a legitimately long LLM thinking pass while surfacing true
        # hangs an order of magnitude faster.
        IDLE_TIMEOUT_SECONDS = 600

        client = None
        message_count = 0
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

            # Wrap receive_response with idle timeout detection:
            # If no message arrives within IDLE_TIMEOUT_SECONDS, raise TimeoutError.
            response_iter = client.receive_response().__aiter__()
            while True:
                try:
                    message = await asyncio.wait_for(
                        response_iter.__anext__(),
                        timeout=IDLE_TIMEOUT_SECONDS,
                    )
                except StopAsyncIteration:
                    break  # Stream ended normally
                except asyncio.TimeoutError:
                    logger.exception(
                        f"[ClaudeAgentSDK] ⚠️ No response from Claude Code CLI for {IDLE_TIMEOUT_SECONDS}s. "
                        f"Aborting agent loop. Messages received so far: {message_count}"
                    )
                    if cli_stderr_lines:
                        logger.exception("[ClaudeAgentSDK] CLI stderr:\n" + "\n".join(cli_stderr_lines))
                    raise TimeoutError(
                        f"Claude Code CLI did not respond for {IDLE_TIMEOUT_SECONDS} seconds. "
                        f"The service may be overloaded or unresponsive. Please try again."
                    )

                # Check cancellation before processing
                if cancellation is not None and cancellation.is_cancelled:
                    logger.info(f"[ClaudeAgentSDK] Cancellation detected after {message_count} messages, stopping")
                    break

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
            if client is not None:
                try:
                    await client.disconnect()
                except RuntimeError as e:
                    if "cancel scope" in str(e):
                        logger.debug(f"Ignoring cancel scope error during cleanup: {e}")
                    else:
                        raise
                except Exception as e:
                    logger.warning(f"Error during client disconnect: {e}")

