---
code_file: src/xyz_agent_context/agent_framework/xyz_claude_agent_sdk.py
last_verified: 2026-06-10
stub: false
---

## 2026-06-10 — Neutral reasoning params → Claude dialect (L1c)

`_resolve_reasoning_options(thinking, reasoning_effort)` maps the
framework-neutral slot params (carried on `ClaudeConfig` from the agent
slot) to ClaudeAgentOptions kwargs: `on`→`{"type":"adaptive"}`,
`off`→`{"type":"disabled"}`, effort low/medium/high/max passes 1:1 via
`effort=`; `""` (auto) emits nothing so the CLI keeps its defaults —
byte-identical behavior to before when unconfigured. Out-of-vocabulary
values (corrupted state) degrade to auto with a warning, never raise.
Per rule #15 the values are passed even to non-Claude proxies; the
`Provider config` log line now includes `thinking=`/`effort=` for
post-hoc grep. Tests: tests/agent_framework/test_claude_reasoning_mapping.py.

## 2026-06-10 — L1a cleanup (SDK 0.1.43 alignment)

Three obsolete-workaround removals after auditing the installed
claude-agent-sdk 0.1.43 against the official Agent SDK docs (research:
`reference/self_notebook/specs/2026-06-10-claude-agent-sdk-adapter-research.md`):

1. **`_safe_parse_message` monkey-patch DELETED.** 0.1.43's parser
   natively returns `None` for unknown message types ("Forward-compatible:
   skip unrecognized message types") and both call sites filter `None`.
   The patch was also ineffective on the main path: `_internal/client.py`
   binds `parse_message` at import time, so reassigning the module
   attribute never reached it. Removing it also drops both
   `claude_agent_sdk._internal` imports from this file's import block.
2. **`max_turns=0` → `None`.** The transport emits `--max-turns` only for
   truthy values, so 0 meant "unlimited" by accident. If upstream ever
   switches to `is not None`, 0 becomes a zero-turn hard cap on
   agent_loop (铁律 #14 violation). None is the documented unlimited.
3. **pyproject pin `>=0.1.6` → `~=0.1.43`.** This file still deliberately
   reaches into SDK internals in two places (`_transport._process` for the
   stall probe and the SIGKILL disconnect fallback — both re-verified as
   still necessary on 0.1.43: `transport.close()` remains terminate() +
   unbounded wait()). A loose pin lets `uv lock` drift the SDK (and its
   bundled CLI — 0.1.43 ships CLI 2.1.56) under those private-attr reads.
   Upgrades are now explicit via `uv lock --upgrade-package`.

## 2026-05-22 — stall health-probe diagnostic (#7, partial)

The silent-probe cadence (`IDLE_PROBE_SECONDS`) now reads
`settings.llm_stall_probe_after_seconds` (.env-tunable). When a run is truly
silent that long AND the CLI subprocess is alive, we now ALSO fire
`_probe_provider_reachable(base_url, …)` — a cheap out-of-band request to the
provider endpoint — and log "provider REACHABLE (model thinking)" vs
"UNREACHABLE (connection dead)". This is the dead-vs-thinking signal for a
prolonged silence. **Diagnostic only — it never force-stops the run** (铁律
#14); the transport-level recovery is the per-request `API_TIMEOUT_MS` + CLI
retry (set via `api_config.to_cli_env`).

**Deferred (NOT yet implemented):** the active `interrupt()` + re-issue
auto-recovery on a confirmed-dead provider (the discussed "路径2"). It's risky
surgery on this shared streaming loop and needs integration testing against a
mock stalled provider before shipping — see
`reference/self_notebook/todo/2026-05-22-debug-batch.md` #7.

## 2026-05-19 — Source-aware history truncation

Replaced the old "append history → `[:100_000]` the whole string" eviction
with a source-aware loop that PROTECTS the system prompt. Background
trigger rows (`_source ∈ {job, message_bus, lark, callback}`) are
dropped oldest-first; chat rows are only dropped once all background
rows are gone. Implementation reads `_source` (set by
[[context_runtime.py]] from `meta_data.working_source`) — DB rows are
never modified, this only governs what gets sent to the LLM this turn.
Belt-and-braces char + UTF-8 byte ceilings stay as last-resort guards
for the case where the system prompt itself overruns argv. Fixes the
"system instructions tail gets chopped" bug observed when history grew
large enough to push the combined string past 100K chars.

## 2026-05-19 — IDLE_TIMEOUT replaced with IDLE_PROBE (铁律 #14)

`IDLE_TIMEOUT_SECONDS = 600` used to `raise TimeoutError(...)` whenever
the CLI emitted no message for 10 minutes. This was a hard cap on
`agent_loop` and violated 铁律 #14 — DeepSeek-V4-Pro CoT and other
long-thinking models legitimately produce minutes-long silent passes,
and memory `agent_long_silence_deepseek` (2026-04 notes) already
recorded this as a known false positive.

Renamed to `IDLE_PROBE_SECONDS` and turned into a *probe* cadence
rather than a kill switch:

1. Every IDLE_PROBE_SECONDS of silence, peek at the CLI subprocess
   `_transport._process.returncode`.
2. `returncode is None` (alive) → `logger.warning("...continuing to wait")`
   and re-enter `asyncio.wait` with the **same** in-flight
   `message_task` (so the SDK's `__anext__()` isn't lost across the
   probe).
3. `returncode is not None` (subprocess actually exited) → log ERROR
   and `raise RuntimeError(...)` — this is a genuine failure, not LLM
   thinking time.

Mechanical changes that follow from "keep message_task across
iterations":

- The per-loop `finally:` now cancels only `cancel_task` (per-iteration);
  `message_task` is owned by the outer function-scope `try`.
- The function-scope `try` hoists `message_task: asyncio.Task | None =
  None` before its first use so the outer `finally:` can cancel + drain
  it without NameError even if `connect()` raised early.
- `message_task = None` is assigned at every consume site (after
  `.result()`, after `StopAsyncIteration`, after cancellation, after
  the subprocess-dead path) so the next iteration creates a fresh task.

## 2026-05-13 — Phase A C1+C2 (race-with-cancel + SIGKILL fallback)

### Race-with-cancel receive loop

Receive loop 从 `asyncio.wait_for(__anext__(), IDLE_TIMEOUT_SECONDS)`
改成 `asyncio.wait([message_task, cancel_task], FIRST_COMPLETED, timeout=IDLE_TIMEOUT_SECONDS)`。

- 两个 awaitable：`response_iter.__anext__()` 和 `cancellation.await_cancelled()`
- 先完成的赢；未完成的在 finally 里强制 `task.cancel()` 避免悬挂
- 如果 cancel 赢了 → `is_cancelled` 是 True → break
- 如果都没在 timeout 内完成 → 旧的 idle-timeout 兜底（认为 CLI 卡死，raise TimeoutError）
- 如果 message 赢了 → 正常 `.result()` 取出（包括 StopAsyncIteration 自然结束）

**关键修复 effect**：cancel 在 tool call 进行中（没有 message 流出）也能即时
检测到。Xiong 那种 13min run 中途 stop 不再被 receive loop 卡住。

### SIGKILL fallback in disconnect

`finally: await client.disconnect()` 改成 `await asyncio.wait_for(client.disconnect(), 5.0)`，
TimeoutError 时通过 `client._transport._process.kill()` 直接 SIGKILL Claude CLI 子进程。

原因：claude_agent_sdk transport.close() 内部 `terminate()` + 无限 `wait()` —
如果 Claude CLI 忽略 SIGTERM 或卡 cleanup 永远不返回。代价是 reach into 第三方
SDK 的私有属性（transport._process），但这是唯一保证 finite-time 子进程回收的
方式。

# xyz_claude_agent_sdk.py — Claude Code CLI 主 Agent Loop 适配层

## 为什么存在

Claude Code CLI 是一个独立的命令行工具，通过 `claude_agent_sdk` Python SDK 以子进程方式驱动。这个文件把 SDK 的低级接口（connect/query/receive_response）封装为系统期望的 `async generator` 接口，并处理：多轮对话历史拼接到 system prompt（CLI 不原生支持多轮）、流式消息格式转换（通过 `output_transfer.py`）、`tool_call_id` 去重（`include_partial_messages=True` 导致的重复事件）、取消信号传播、空消息检测、idle timeout。

## 上下游关系

被 `step_3_agent_loop.py` 调用，在 Step 3.4 中启动 agent loop，接收所有流式事件并 yield 给上层。上层拿到的事件由 `response_processor.py` 解析为类型化消息。

配置通过 `api_config.claude_config`（ContextVar proxy）获取，确保每个 asyncio task 使用 owner 的配置。MCP 服务器 URL 由调用方传入（`mcp_server_urls`），包含所有激活 Module 的 MCP 端点。

`output_transfer.py` 是直接依赖，把每条 Claude SDK 消息转换为事件列表后才 yield。

## 设计决策

**多轮对话拼接到 system prompt**：Claude Code CLI 的 `ClaudeAgentOptions` 不支持 messages 数组，只有 `system_prompt` 和单条 `query`。所以所有历史对话都被格式化为文本追加到 system prompt 末尾，超出 60KB 时截断保留最近部分。这是已知限制，等 SDK 支持 multi-turn 后可以去掉。

**`_safe_parse_message` monkey-patch**：已于 2026-06-10 删除（见顶部 L1a 条目）——SDK 0.1.43 原生跳过未知消息类型，patch 在主路径上本就未生效。

**`NO_PROXY` 和 `CLAUDECODE` 环境变量注入**：系统代理可能导致 Claude CLI 子进程访问 localhost MCP 服务器走代理返回 502。`CLAUDECODE=""` 是为了防止嵌套 Claude Code 会话检测阻止子进程启动（当后端在 Claude Code 终端内运行时）。

**`max_buffer_size=50MB`**：MCP 工具（如 PDF 解析）可能返回大量内容，默认 buffer 太小会导致响应被截断。

**600 秒 idle timeout**（Bug 20, 2026-04-20 从 1200s 下调）：用 `asyncio.wait_for` 包装每次 `__anext__()`，超过 10 分钟 CLI 静默则抛 TimeoutError。原来 1200s 是基于"给 MCP tool call 足够空间"的保守估计；事故后每个 MCP 工具 handler 通过 `with_mcp_timeout` 自限在 ≤60s，Claude CLI 内置 tool 自己有更短 timeout，**真实工作下 600s 静默 = 一定出 bug**，早点 TimeoutError 让错误更快现形。

**两道 system_prompt 上限：char ceiling + UTF-8 byte ceiling**（2026-04-22 调整）：
Python SDK 用 `--system-prompt <str>` argv 传 prompt 给 `claude` CLI；Linux
`MAX_ARG_STRLEN = 128 KiB`（x86_64 典型）。旧版只按 `len()` 字符数限制到 60K，对
纯英文安全，但对中文（UTF-8 3 bytes/char）理论最坏只能承载 ~42K 字节。T8 禁用
ToolSearch 后，非 Claude 模型的 system prompt 常态化到 60-80K chars（全量 MCP
工具 schema），60K 限制频繁截断。现在改成两道闸：
- **MAX_SYSTEM_PROMPT_LENGTH = 100_000 chars**：给 T8 场景留出 20-40K 余量
- **MAX_SYSTEM_PROMPT_BYTES = 120 KiB**：encode('utf-8') 后超出则按字节二次截断，
  `decode('utf-8', errors='ignore')` 丢掉被截断的半字符，保证输出始终是合法 UTF-8。
- **MAX_HISTORY_LENGTH = 50_000 chars**（从 30K 上调）：让 MiniMax 多轮场景保留
  更多历史。history 在进入 system_prompt 前单独预截断，与总长限制正交。

**按模型名决定是否启用 ToolSearch / deferred tool loading**（2026-04-22 引入）：Claude Code CLI 在工具总量超过 `ToolSearchCharThreshold` 时自动启用 deferred tool loading —— 给 LLM 一个工具索引，具体 schema 通过 `ToolSearch(select:X)` 按需加载并以 `tool_reference` block 返回。这个协议是 Claude Sonnet-4+ / Opus-4+ 的扩展，**非 Claude 模型（MiniMax / GPT / Gemini 等）通过 Anthropic-compatible 代理调用时看不懂 `tool_reference`**，表现为 LLM thinking 里抱怨 "the tool registry is not finding the chat module send_message tool"、整段 turn 静默结束（Pattern A 的硬证据见 TODO-2026-04-22 T7）。现在根据 `claude_config.model` 是否以 `claude-` 开头在 `cli_env` 组装时做决策：Claude 原生模型走 CLI 默认 `auto` 模式继续享受 deferred 省 token 收益；非 Claude 模型显式 `ENABLE_TOOL_SEARCH=false`，CLI 把所有工具全量暴露给 LLM、不再依赖 `tool_reference`，MiniMax 等模型可稳定 invoke。决策同步写进 `Provider config` 日志行的 `tool_search=` 字段，方便事后 grep。

**`build_tool_policy_guard` 注入 PreToolUse hook 做沙箱**：CLI 本身没有工作空间隔离概念，也不知道 WebSearch 需要 Anthropic 服务端工具。我们在这里装一个 hook（`_tool_policy_guard.py`），在云端部署下强制 Read/Glob/Grep 只能访问 workspace、Bash 不允许全局安装（brew/npm -g/apt/sudo/裸 pip），在任何模式下把 `lark-cli` shell-out 重定向到 MCP、把无 server-tool 的 provider 调 WebSearch 拦下来改用 WebFetch。hook 在 `permission_mode="bypassPermissions"` 之前触发，所以即使 bypass 也生效。`HookMatcher` 的 `matcher` 必须覆盖 `Read|Glob|Grep|WebSearch|Bash`。

## Gotcha / 边界情况

- `include_partial_messages=True` 导致 partial 和 complete `AssistantMessage` 都携带 `ToolUseBlock`，同一 `tool_call_id` 会出现两次。去重通过 `seen_tool_call_ids` set 在这里处理，`output_transfer.py` 不处理去重。
- 0 条消息收到时 log error 但不抛出异常——调用方会收到一个空 `final_output` 的 `PathExecutionResult`。这是静默降级，可能让用户看到空回复而不是错误提示。
- `client.disconnect()` 在 cancel scope 错误时被静默忽略（anyio cancel scope 兼容性问题），正常 RuntimeError 仍会抛出。
- **ToolSearch 判断依赖 `claude_config.model` 非 None 且以小写 `claude-` 开头**。如果某调用路径没给 model（slot 配置缺失 / 默认 fallback），`(claude_config.model or "")` 为空 → `startswith("claude-")` 为 False → 走非 Claude 分支禁用 ToolSearch。这是安全方向 fallback：宁可多烧一点 token 也要保证工具可调用。若未来接了大写写法或别名的 provider，需扩展这条判断而不是简单复制。

## 新人易踩的坑

- `this_turn_user_message = (messages.pop())["content"]`：这里假设最后一条消息是 user message。如果调用方构建 messages 时最后一条不是 user message，会产生逻辑错误。代码注释里也标注了这个 TODO。
- 直接在本地测试时，`claude` CLI 必须已经登录（`claude auth login`），否则会收到 0 条消息且没有明显错误——只有 stderr log 里有认证失败信息。
