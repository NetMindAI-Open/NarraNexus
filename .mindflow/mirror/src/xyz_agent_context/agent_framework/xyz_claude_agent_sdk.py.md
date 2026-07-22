---
code_file: src/xyz_agent_context/agent_framework/xyz_claude_agent_sdk.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — 子进程 provider 可观测日志(Lark bug #1)

`agent_loop` 组装完 `cli_env`(所有覆盖之后、build options 之前)加一条 INFO,打印
**生效**的 `base_url`/auth 种类/`CLAUDE_CONFIG_DIR`。与之前那条"配置意图"日志区分:
这条是**实际注入子进程的 env**。用途:个人 `~/.claude/settings.json` 的 env-block
可把 `ANTHROPIC_BASE_URL` 悄悄改道到私有 relay,以前无迹可查(2026-07-08 事故靠 30+
次黑盒探测才定位);现在配置 vs 实际一 grep 即比对。纯可观测,无行为变更;只打
base_url 与 auth 种类(token/key/none),不打凭据本身。CLI helper 侧有对称日志
(见 [[cli_helper_sdk]])。

## 2026-07-15 — MCP spec 带自定义 headers（`_build_claude_mcp_config`）

`agent_loop` 第二参数改为 `mcp_servers: {name: {"url", "headers"?}}`。新增模块级
`_build_claude_mcp_config()`：spec → `McpSSEServerConfig`，有 headers 才带
`headers` 键（SDK 0.1.43 起原生支持，连接时随请求发送）。模块内部 MCP 不带
headers，行为不变。headers 值是密钥，函数内不打日志。

## 2026-07-14 — inline `AssistantMessage.error` 把 CLI stderr 折进错误事件（病A / "黑盒" P1）

`AssistantMessage.error` 是**只有 6 个值的枚举**（auth/billing/rate_limit/
invalid_request/server_error/unknown）。真正的 provider 原因——例如 litellm
`ContextWindowExceededError: inputs 75307 > 32769`——被 CLI 压成这个枚举，
数字只活在 **CLI stderr** 里。原本 inline error 分支只 `logger.error` 打日志，
让 `output_transfer` 输出干巴巴的 `Claude API error: unknown`，真相丢失。

现在:inline error 分支里，当 `cli_stderr_lines` 非空，改为 yield
`_inline_assistant_error_event(message.error, cli_stderr_lines)` 并 `continue`
（跳过 output_transfer 的枚举事件）。该 helper 保留 `error_type`=枚举原值、把
stderr 尾部折进 `error_message`——复用 `_zero_output_error_event` 的
`_stderr_tail_detail` 共享写法。这样下游
`llm_failure.classify_self_serviceable` 能从 message 文本认出 context-window /
余额 / 模型错误。stderr 为空时不加东西，让 output_transfer 的枚举事件照常走
（有些 inline error 的 stderr 本就是空的）。

## 2026-07-12 — macOS 上**陈旧 host 文件遮蔽 Keychain**:凭据来源改为 Keychain 优先

**症状**:本地版重新 `claude login` 后,Nexus 仍报 "coding-agent login has expired";
Backend log 里 CLI 实为 `AssistantMessage(text='Not logged in · Please run /login')` +
`error='authentication_failed'`。本地 `claude` CLI 正常,唯独 Nexus agent 槽失败。

**真正根因**(比"stage-once"更底层):这台机器**同时存在两份凭据**——
- 陈旧 host 文件 `~/.claude/.credentials.json`(6-25、`expiresAt` 已过期、只有 3 个 key 的旧格式);
- 新鲜 Keychain(`Claude Code-credentials`、`expiresAt` 未过期、6 个 key 的现代格式,含
  `scopes/subscriptionType/rateLimitTier`)。

现代 macOS Claude Code 只写 **Keychain**;那个 `~/.claude/.credentials.json` 是老版本 CLI 的
**遗留物**。但 `_stage_claude_oauth_credentials` 原逻辑是"host 文件存在就用它,否则才回退
Keychain":`if source.is_file(): <copy2 file>`。于是 `source.is_file()==True`(陈旧遗留物)
**永远遮蔽** Keychain,把 6-25 的过期 token `copy2`(保留 mtime,故隔离副本 mtime 也是 6-25)
进隔离目录 → 隔离 CLI 读到过期文件 → "Not logged in"。用户自己的 `claude` 读 Keychain 所以正常。

**修复**:macOS 上 **Keychain 是唯一权威**,host 文件仅当 Keychain 无 entry 时才作后备。
- 新增 `_oauth_expires_at(blob)`:解析 `claudeAiOauth.expiresAt`(epoch-ms;**绝不 log blob**)。
- 新增 `_read_keychain_blob()`:`security find-generic-password -s "Claude Code-credentials" -w`
  的可 mock 封装,无 entry / 读失败 → None。
- 新增 `_stage_blob_newest_wins(dir, blob, sourced_from=…)`:按 `expiresAt` 的 newest-wins 原子
  写(0600)。仅当源严格更新才重导;隔离副本 expiresAt >= 源 → 保留(护住 CLI 就地刷新,不重新
  注入已消费 refresh token,仍规避 #76 登出);源无 expiresAt → 绝不覆盖好副本。
- `_stage_claude_oauth_credentials`:`if sys.platform=="darwin": kc=_read_keychain_blob();
  if kc: _stage_blob_newest_wins(...); return`——Keychain 有就用,永不被陈旧 host 文件遮蔽;
  否则落到原 host-file 路径(copy2 + mtime newest-wins),**Linux/云端逐字不变**(无 Keychain)。

**代价**:macOS 每次 spawn 多跑一次 `security`(约 10ms)。为正确性接受。
守卫测试(`tests/agent_framework/test_claude_config_isolation.py`):
`test_darwin_keychain_wins_over_stale_host_file`(本次回归 · 核心)、
`test_darwin_falls_back_to_host_file_when_keychain_empty`(老版 CLI 后备)、
`test_stage_blob_newest_wins_restages_when_newer` /
`test_stage_blob_preserves_inplace_refresh`(newest-wins 两个方向)、
host-file 两测已 mock `_read_keychain_blob` → None 以在 dev Mac 上确定性走文件路径。

## 2026-07-09 — macOS: OAuth 凭据从 Keychain 导出进隔离目录(#76 的 macOS 补丁)

#76 把 claude OAuth 隔离进独立 `CLAUDE_CONFIG_DIR`(`claude_oauth_config_path`)
并把 `~/.claude/.credentials.json` **拷**进去。但 macOS 上 claude 把 OAuth token 存
**Keychain、没有那个文件** → 文件拷贝 no-op、隔离目录空;而显式设了 `CLAUDE_CONFIG_DIR`
又让 CLI 走文件模式忽略 Keychain → "Not logged in"(真机实测)。

新增 `_stage_claude_oauth_from_keychain(config_dir)`:`_stage_claude_oauth_credentials`
在**源文件缺失且 `sys.platform=="darwin"`** 时调它——用 `security find-generic-password
-s "Claude Code-credentials" -w` 读出 Keychain 凭据,原子写成隔离目录里的
`.credentials.json`(0600,**绝不 log 内容**)。**darwin-only**:Linux/云端那个源文件存在,
永远走不到此分支,行为与 #76 逐字一致(零云端影响)。

**stage-once**(非 newest-wins):~~Keychain 无 mtime 可比,且每次 spawn 重导会覆盖 CLI
在隔离文件里刷新过的 token(重新注入已消费的 refresh token → 登出,正是 #76 newest-wins
要避免的)。故仅在隔离文件缺失时导出一次。~~ **⚠️ 2026-07-12 起已废弃 stage-once,改为按
`expiresAt` 的 newest-wins,见文件顶部条目**——原设计的"代价"(重新 `claude login` 后需手删
隔离目录)正是那次的修复目标。安全面:token 是本人本机、0600,与 codex 的明文
`~/.codex/auth.json`、claude-on-Linux 的 `.credentials.json` 同级。

`CliHelperSDK._run_claude_oneshot` 也会调 `_stage_claude_oauth_credentials`(见
[[cli_helper_sdk]]),使 claude helper 自足——agent 槽是 codex 或后台单独调 helper 时
隔离目录也能被 seed。

## 2026-07-09 — `_stage_claude_oauth_credentials`(OAuth 隔离目录的凭据搬运)

OAuth 的 `CLAUDE_CONFIG_DIR` 现在指向独立目录
`settings.claude_oauth_config_path`(见 [[api_config]] 2026-07-09 条),不再是
宿主 `~/.claude`。`agent_loop` 在 `to_cli_env()` 之后、spawn 之前,若
`auth_type == "oauth"` 就调用这个新的模块级函数,把宿主
`~/.claude/.credentials.json`(经 `provider_driver.derive.resolve_claude_credentials_path`
解析,尊重 `CLAUDE_CLI_CREDENTIALS_PATH`/`CLAUDE_CLI_HOME` 覆盖)**单文件**拷进隔离目录。
只拷 `.credentials.json`、绝不拷 `settings.json` —— 后者的 `env` 块正是劫持源。

**newest-wins**:仅当宿主副本比已暂存副本更新(或副本缺失)才覆盖;否则保留 CLI 在
隔离目录里就地刷新过的 token(避免把已轮转作废的旧 refresh token 回灌、把用户登出)。
宿主无凭据文件 → warn + no-op,不抛错。对齐 Codex 的 `_stage_codex_oauth_credentials`
(那边是 per-run temp `CODEX_HOME`;Claude 这边用持久隔离目录,与 keyed 路径同风格,
故用 newest-wins 而非每次覆盖)。守卫测试见
`tests/agent_framework/test_claude_config_isolation.py`。

**原子落盘(必须)**:`claude_oauth_config_path` 是**所有 OAuth agent_loop 共用的固定
目录**(不是 Codex 那种 per-run temp),staging 那一刻隔离目录里可能正好有一个 CLI 在读
`.credentials.json`。裸 `shutil.copy2(source, dest)` 会先 truncate `dest` 再写,重新打开
了本 fix 要堵的「半读 / 并发写」窗口(与当初 `~/.claude/.claude.json` 在 55KB↔50 字节
反复横跳同形)。所以落盘走**同目录临时文件 + `os.replace`**(POSIX 原子 rename);`copy2`
保留 mtime,rename 后 newest-wins 仍成立;`chmod(0o600)` 在 rename **之前**做,避免 `dest`
短暂出现 0644。

**已知代价 — 宿主可能被登出(单向拷贝的取舍)**:staging 是**单向** 宿主 → 隔离目录,
没有回写。若隔离目录里的 CLI 就地刷新了 OAuth token,宿主 `~/.claude/.credentials.json`
仍留着已被服务端轮转作废的旧 refresh token,用户自己的交互式 `claude` 在 access token
过期后拿旧 refresh token 去刷 → 401 → 被登出、需重新 `claude auth login`。DMG 模式下
agent_loop 与宿主是同一个人,体感尤其差,且只在数小时后 token 过期时才炸、难归因。
这是**已接受的取舍**,与 Codex 单向 `_stage_codex_oauth_credentials` 一致——当前不做
token 回写(真要回写,也必须走同样的原子 rename,否则回到上面「原子落盘」那条)。下一个
碰到「宿主被登出」的人:这是设计取舍,不是 bug,别再重推一遍这条链。

## 2026-07-03 — MAX_SYSTEM_PROMPT_LENGTH bumped 100K → 115K

Symptom-treatment for a bloated system_prompt observed on live agent
`agent_62cf67080ad4`: assembled prompts clocked in at 91–93K chars
across five consecutive turns, leaving only ~5–8K of the 100K char
budget for history. Source-aware eviction was dropping 20–23 of ~29
history rows on every turn, starving the LLM of NarraMessenger
context (silent-ingested rows in particular, since they're keyed
`_source != "chat"` and drop in Tier-1).

Direct cause: `SKIP_MODULE_DECISION_LLM = True` forces the loader to
inline all 15 modules' `get_instructions()` on every turn, regardless
of relevance. Sampled sizes: ChatModule 13K, CommonTools 8K, Slack 8K,
MessageBus 6K, Telegram 6K, Skill 4K, Discord 3K, Lark 2K,
NarraMessenger 0.8K, WeChat 0.7K, plus BasicInfo / SocialNetwork /
Awareness / Job (not measurable without an active ctx_data but ~15K
combined in production). Total steadily >90K.

115K keeps mixed-CJK content comfortably below
`MAX_SYSTEM_PROMPT_BYTES = 120 KiB` and the 128 KiB argv hard limit,
and gives history 20–30K of budget instead of 5–8K — enough to
retain the last full turn on IM channels where history rows are
long. This is TREATMENT, not cure; the root fix is a
module-selection loader that only inlines instructions relevant to
this turn's channel/context (deferred as a separate follow-up per
the design note added inline at the constant's block comment).

## 2026-07-03 — 0-message run emits a classifiable error (no more silent fallback)

When the Claude CLI yields 0 messages (expired OAuth / not logged in / crash /
quota) the generator used to only log and end, so the pipeline read no-messages
as "agent chose not to reply" and the helper-LLM fabricated a hollow fallback —
the Owner reported "mysterious fallback, no error". It now yields
_zero_output_error_event (a response.error carrying the raw CLI stderr).
Classification stays in response_processor._is_auth_failure: an auth/login
stderr becomes a fatal AUTH_EXPIRED (re-login prompt, no_reply fallback skipped);
anything else stays a recoverable no-output error. The base sentence is kept
auth-phrase-free so an empty stderr is never misclassified as auth. Guarded by
tests/agent_framework/test_zero_output_error_event.py.

## 2026-07-03 — main-loop model normalized via `resolve_cli_alias` (upstream #57)

`options_kwargs["model"]` passes through `resolve_cli_alias(model,
auth_type)`: bare family aliases become full ids on api_key/bearer
transports, stay verbatim on OAuth. Complements the earlier
`_is_claude_native` fix (906312b5) which only adjusted tool policy, not
the model string itself.

## 2026-06-11 — thinking 走 --effort,绝不发 --max-thinking-tokens 正数

CC 在当前代 Claude 模型上每轮 400(`"thinking.type.enabled" is not supported
for this model`,被 `AGENT-LOOP-RECOVERABLE: Claude API error: unknown` 盖住)。
**根因不在我们的 API 形状,而在 SDK→CLI 的翻译链**(2026-06-11 实测
`claude_agent_sdk/_internal/transport/subprocess_cli.py` + CLI 2.1.x):

1. SDK **把 `ClaudeAgentOptions.thinking` 全翻成 `--max-thinking-tokens N`**
   (adaptive→32000、enabled→budget、disabled→0),**从不发 `--thinking
   adaptive`**;
2. Claude Code CLI 把**正数的 `--max-thinking-tokens`** 当成旧版
   `thinking:{type:"enabled",budget_tokens:N}` 发给 API → 当前模型 400;
3. CLI 唯一的 adaptive 开关是 **`--effort <level>`**(`--help` 仅此一个;
   无 `--thinking`)。给 `--effort` 且不给 `--max-thinking-tokens` → adaptive;
   什么都不给 → 退回被拒的 enabled。

> 之前一版误以为"把 `thinking` 设成 adaptive dict"就行——错。SDK 会把它
> 变成 `--max-thinking-tokens 32000`,CLI 照样发 enabled,还是 400。

**正解**(`_resolve_reasoning_options`):
- **on / auto / 未知** → 只回 `{"effort": <level>}`,**不带 `thinking` 键**
  (SDK 因此不发 --max-thinking-tokens,CLI 走 adaptive)。auto/未知 effort
  兜底 `"high"`(Anthropic server 默认),**保证 --effort 一定在**——没有任何
  flag 时 CLI 会退回 enabled。
- **off** → `{"thinking": {"type": "disabled"}}`(→ --max-thinking-tokens 0,
  唯一不 400 的 max-thinking-tokens 值;off 时不带 effort)。

我们任何路径都不产生正数 --max-thinking-tokens,故永不发 enabled。
版本背景:PATH `claude` 2.1.39 / SDK bundled 2.1.56,两者都靠 --effort 走
adaptive,此改法版本无关。局限:故意 pin 只认 enabled+budget_tokens 的旧模型
(如 Sonnet 4.5)在此拿不到思考预算——平台面向当前模型。测试:
tests/agent_framework/test_claude_reasoning_mapping.py。

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
