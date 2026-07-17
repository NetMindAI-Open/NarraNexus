---
code_file: src/xyz_agent_context/context_runtime/context_runtime.py
last_verified: 2026-07-15
stub: false
---

## 2026-07-15 — `build_input_for_framework` 返回 MCP spec dict

返回值第二项从 `{name: url}` 改为 `{name: {"url": url}}`（模块内部 MCP 无
headers）；用户外部 MCP 的 headers 由 backend 装配层（websocket/skills）注入
`pass_mcp_servers`。命名统一 `mcp_servers`。

## 2026-07-14 — [SYSPROMPT-BREAKDOWN] 诊断日志（system-prompt-growth 事故取证）

`build_complete_system_prompt` 现在在 return 前打一条 INFO
`[SYSPROMPT-BREAKDOWN] agent=… total=… | parts: security/temporal/narrative/modules/bootstrap=各字节 | narrative: nar_summary_chars/nar_dynamic_entries | top_modules: 最大 5 个模块指令`。
纯诊断、不改行为。**动机**：观测到 system prompt 逐轮增长(app ~100k、dev ~115k 上限
`MAX_SYSTEM_PROMPT_LENGTH`),逼近上限后历史被驱逐、agent(含原生 opus)停止调
`send_message_to_user_directly`。此前每 Part 字节只在 `logger.debug`(生产 INFO 级看不到)。
新增纯静态 helper `_log_system_prompt_breakdown`(可单测,见
`tests/context_runtime/test_system_prompt_breakdown.py`)。narrative 的 `current_summary`
字节 + `dynamic_summary` 条数是增长头号嫌疑,单独打出来量化。

## 2026-07-10 — 移除写死的假模型身份（改由 BasicInfoModule 动态填）

`run()` 构造 `ContextData` 时曾写死 `agent_info_model_type="Claude Agent SDK"` +
`model_name="sonnet-4"`，经 basic_info [[prompts.py]] 的 "LLM Model" 段灌进系统
prompt → **每个** agent（含 codex_cli+gpt5）都自称 Claude Sonnet-4，被问模型就照读
（违反铁律#9）。两行 kwargs 已删；这两个字段改由 [[basic_info_module.py]]
`hook_data_gathering` 经 [[agent_model_identity.py]] 按真实 slot 动态填。
ContextRuntime 从此不掺和模型身份（本就不该知道），字段也在 [[context_schema.py]]
正式声明了。

## 2026-07-09 — current-turn attachment marker injection

`build_input_for_framework` 追加"当前 turn user message"时，读 `ctx_data.extra_data["attachments"]`，通过 `Attachment.markers_from_dicts(agent_id=ctx_data.agent_id, user_id=ctx_data.user_id)` 合成 marker 拼在 LLM 视图的 content 尾部。**关键：不动 `ctx_data.input_content`**——那个字符串会被 `ChatModule.hook_persist_turn` 原样写成用户消息的 `content`，`backend/routes/agents_chat_history.py` 又会把它回显到前端。marker 只走 LLM 视图，绝对路径不进 UI 也不进 DB。

`ctx_data.user_id` 已被 `AgentRuntime` 覆写为 agent owner（`_agent.created_by`，agent_runtime.py:245），marker 里的路径拿到的就是 owner workspace 的绝对路径——跟 trigger 落盘时的路径一致，agent Read 直接命中。

覆盖范围：所有 IM 渠道 + WS 前端 chat 都已把 `attachments` 放进 `trigger_extra_data`（channel_trigger_base.py 的 `_build_and_run_agent` line 1175-1178；backend/routes/websocket.py line 679），零 trigger 侧改动。下一轮读历史时 `ChatModule._synthesize_attachment_markers`（同一 `Attachment.markers_from_dicts` 底层）再合成一次，marker 格式两条路径完全一致，agent 行为在当前 turn vs 历史 turn 上无差别。

Regression：`tests/channel/test_current_turn_attachment_marker.py` 5 条锁死"注入不动 input_content / owner routing / malformed 有 WARNING / 空列表零动作"。

## 2026-06-17 — system prompt 第一段注入安全铁律(**云端专属**)

`build_complete_system_prompt` 在所有其它段(temporal / narrative /
module / bootstrap)之前 append `prompts.SECURITY_IRON_RULES`,确保没有后续
段落或用户消息能覆盖它。**仅当 `get_deployment_mode()=="cloud"` 时注入** ——
铁律是多租户保护;本地/桌面是用户自己的机器,用户就是要 agent 跨自己的文件夹
干活,注入它会废掉本地体验,且本地没有别的租户/平台密钥要保护。详见
`prompts.py.md`。

## 2026-06-12 — User Identity Context block REMOVED (治本: moved into basic_info)

The `_build_user_identity_block` method, its "Part 0b: User Identity" injection
in `build_complete_system_prompt`, and the `USER_IDENTITY_CONTEXT` import are
all gone. That block was a redundant second place to inject owner/sender
identity — the canonical identity injection lives in [[basic_info_module.py]]
(`hook_data_gathering` + basic_info `prompts.py`), which is where the human-name
fix now lives. Removing it avoids two competing identity sources in the system
prompt. See the 2026-06-11 entry below for what the now-deleted block did.

## 2026-06-11 — User Identity Context block (owner + sender, by human name)

build_complete_system_prompt now injects a "Part 0b: User Identity" block via new `_build_user_identity_block(ctx_data)`: states the agent OWNER by display_name (NetMind nickname / local display_name; falls back to user_id, never shown as a name otherwise), and — when the trigger carries `sender_user_id` in extra_data (only chat does) — whether the current sender is the owner or a visitor (resolves their display_name, compares to owner). IM triggers don't set sender_user_id (their own module trust block handles sender), so they get only the owner line; job/bus likewise. Cleanly separates user_id (opaque scoping key) from the human name. Defensive: lookup failure never breaks the prompt.

last_verified: 2026-05-29
stub: false
---

## 2026-05-29 — EverMemOS removed

The "Relevant Memory" prompt section is gone. `build_complete_system_prompt`
and `run()` no longer take `relevant_episodes`; `_build_relevant_memory_prompt`
was deleted; `_build_auxiliary_narratives_prompt` no longer takes
`evermemos_memories` (it now renders only the auxiliary narrative summaries).
System prompt is now: temporal context → main narrative → module instructions
→ bootstrap. Long-term memory is the current narrative's full history, surfaced
by [[chat_module.py]] as the unified timeline (see note below).

## 2026-05-20 (Fix #2 P1) — render the unified timeline; drop the cross-narrative system-prompt section

`build_input_for_framework` no longer splits chat_history into long/short and
no longer injects cross-narrative memory as a separate system-prompt section
(via `_build_short_term_memory_prompt` + `SHORT_TERM_MEMORY_HEADER` — now
DEPRECATED/unused). It renders the single unified timeline (built by
[[chat_module.py]]) as real role messages, each prefixed by
`_format_timeline_tag()` → `[time · topic · nar_id]` plus the channel source
prefix, and prepends `CHAT_HISTORY_TIMELINE_PREAMBLE` to the system prompt to
teach the agent how to read it (tags, how it was assembled, and what the user
can/can't see — reasoning is private). `[CHAT-CTX] unified timeline rendered`
log line reports total / cross / current counts. `_format_timeline_tag` now also
emits `evt=<event_id>` per line (for view_event drill-down).

## 2026-05-20 (Fix #2 P2) — recent-actions section in the system prompt

`_build_recent_actions_section` renders `ctx_data.extra_data['recent_actions']`
(populated by [[chat_module.py]] `_load_recent_actions`) as a compact
`RECENT_ACTIONS_HEADER` block appended to the system prompt — one line per
background activity `- [time] <source>: <job title / summary> (evt=<id>)`. Kept
separate from the conversation timeline so background work doesn't pollute it.

## 2026-05-19 — `_source` carried on final_messages

`build_input_for_framework()` now stamps each long-term history row with
an internal `_source` field copied from its `meta_data.working_source`
(default `"chat"`). Consumed by [[xyz_claude_agent_sdk.py]] for
source-aware truncation: when the system prompt + history would exceed
the SDK's argv ceiling, oldest background-trigger rows
(`job / message_bus / lark / callback`) are evicted first; chat rows
are kept until the budget can't be met any other way. Other SDK
adapters (OpenAI Agents, Gemini) build their own message dicts so this
extra key never reaches them.

# context_runtime.py — the assembly engine that turns raw Narrative + Module state into a ready-to-submit LLM payload

## 为什么存在

Before each LLM call, the agent needs a fully formed system prompt and a message list. That assembly is non-trivial: it requires pulling the right Narrative summary, firing every active module's data-gathering hook, sorting module instructions by priority, routing conversation history into two memory tracks (long-term vs. short-term), truncating oversized messages, and collecting MCP server URLs for tool access — all in a deterministic order. `ContextRuntime` owns that entire assembly pipeline so the orchestration layer (`step_3_agent_loop.py`) can hand it a Narrative list and an instance list and receive back a `ContextRuntimeOutput` without knowing anything about how the prompt was built.

Without this class, the assembly logic would bleed into `AgentRuntime` steps, each module would need to know about every other module's output format, and the prompt structure would become impossible to reason about or test in isolation.

## 上下游关系

**Receives from:**
- `step_3_agent_loop.py` (inside `agent_runtime/_agent_runtime_steps/`) is the exclusive runtime caller. It constructs a `ContextRuntime` instance with the `agent_id`, `user_id`, and a `DatabaseClient`, then calls `.run()` with the Narrative list and active module instances produced by earlier pipeline steps.
- `NarrativeService` (`narrative/`) — called inside `build_complete_system_prompt()` to format the main Narrative's summary prompt via `combine_main_narrative_prompt()`.
- `HookManager` (`module/hook_manager.py`) — invoked in `run()` Step 1-2 to fire `hook_data_gathering` on every loaded module, which allows modules like `ChatModule` to populate `ctx_data.chat_history`.
- `AgentRepository` (`repository/`) — queried directly inside the Bootstrap injection block to look up who created the agent, bypassing `BasicInfoModule` to avoid a module-load dependency. **Bootstrap deletion is now profile-driven (2026-06-16)**: the auto-delete threshold is no longer a hard-coded `>= 3` — it comes from `bootstrap.profiles.auto_delete_threshold_from_meta(agent_record.agent_metadata)` (missing key → historical default 3; `None` → never rule-delete, semantic-only). The injection prompt stays the global `BOOTSTRAP_INJECTION_PROMPT`.
- `prompts.py` — all section header strings are imported from the sibling file.
- `schema` (`ContextData`, `ModuleInstructions`, `ContextRuntimeOutput`, `WorkingSource`) — provides the typed containers that flow through the pipeline.

**Consumed by:**
- `step_3_agent_loop.py` — the only caller that constructs and runs `ContextRuntime`. Its output (`ContextRuntimeOutput.messages`, `ContextRuntimeOutput.mcp_urls`, `ContextRuntimeOutput.ctx_data`) is forwarded to the agent framework adapter in subsequent pipeline steps.
- The package's `__init__.py` re-exports `ContextRuntime` under `xyz_agent_context.context_runtime`, but no other module within the package imports it at runtime.

## 设计决策

**Chat history comes from `ChatModule`, not from Event records.** The original design stored conversation turns as `Event` objects and reconstructed the message list from them during context assembly. After the 2025-12-09 refactoring, `ChatModule` (via `EventMemoryModule`) provides `ctx_data.chat_history` directly. The old `extract_narrative_data()` method and the Event History section of `build_complete_system_prompt()` are both commented out rather than deleted — they remain as documented fallbacks while the new approach is validated. This means there are dead code blocks with explicit `TODO` annotations; they are intentional placeholders, not forgotten debris.

**Dual-track memory split inside `build_input_for_framework()`.** Each message in `chat_history` carries a `meta_data.memory_type` tag set by `ChatModule`. Messages tagged `long_term` are placed as ordinary `role/content` pairs in the messages list (chronologically ordered, per-message truncation applied). Messages tagged `short_term` are serialised into the system prompt via `_build_short_term_memory_prompt()` under a dedicated markdown section. This separation exists because the LLM's context window treats the system prompt differently from the message history — short-term cross-topic context is better positioned as background framing than as fake conversation turns.

**Module instructions are deduplicated by `module_class`, not by `instance_id`.** A single module type (e.g., `JobModule`) can have multiple instances (one per job). If each instance contributed its own instructions section the system prompt would contain near-identical paragraphs. Deduplication at the `module_class` level ensures each module type contributes exactly one instruction block, taking its wording from whichever instance is seen first during iteration.

**Bootstrap injection is self-destructing.** The `Bootstrap.md` file is written once by the agent creator to seed initial behaviour. After three Event records exist for the agent, `context_runtime.py` deletes `Bootstrap.md` automatically on the next run. The threshold of three events is a deliberate grace period — the first few turns often include the bootstrap instructions being read and acted upon. If the agent fails to delete the file itself, the auto-delete prevents perpetual bootstrap mode without requiring external cleanup.

**`SINGLE_MESSAGE_MAX_CHARS = 4000`** is a per-message safety cap only. Overall context length management is delegated to the Claude Agent SDK's `MAX_HISTORY_LENGTH` setting. The two limits address different failure modes: per-message truncation prevents a single large paste from dominating the context window, while the SDK's history limit prevents total token overflow across many turns.

**`SHORT_TERM_TOKEN_LIMIT = 40_000` characters (≈ 10k tokens).** Short-term memory is intentionally given a smaller budget than the main message history. Groups are processed in reverse chronological order so the most recent cross-topic context survives budget exhaustion.

## Gotcha / 边界情况

**`run()` always appends the current user input as the final message.** The current turn's `input_content` (from `ctx_data`) is appended to `final_messages` after all history is inserted. If a caller accidentally includes the current turn in the `chat_history` they pass to `ContextRuntime`, the LLM will see it twice — once in the history position and once as the trailing user message. `ChatModule` is responsible for ensuring `chat_history` contains only prior turns.

**Auxiliary Narrative summaries are computed twice if `extract_narrative_data()` is disabled.** The commented-out `extract_narrative_data()` call would have populated `ctx_data.extra_data["auxiliary_narratives"]`. Because it is disabled, `build_complete_system_prompt()` has a fallback that extracts the same summaries directly from `narrative_list[1:]`. Any change to the auxiliary Narrative summary format must be applied in both places (the fallback block and the `extract_narrative_data()` method body), otherwise the two paths will diverge when `extract_narrative_data()` is eventually re-enabled.

**`evermemos_memories` enriches auxiliary Narrative summaries.** If the orchestrator layer passes `evermemos_memories` into `run()`, it gets injected into `ctx_data.extra_data` and later consumed inside `_build_auxiliary_narratives_prompt()` to append "Related Content" snippets. If `evermemos_memories` is `None` (the default), the section appears without enrichment and no error is raised. The enrichment path is Phase 3 functionality; leaving it `None` is the safe default.

**Bootstrap detection performs a raw SQL `COUNT(*)` query.** The Bootstrap injection block bypasses the Repository layer and issues `db.execute("SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s", ...)` directly. This is intentional to avoid pulling in `EventRepository` as a dependency, but it means the query is not covered by the standard repository test harness and will silently return `event_count = 0` if the query fails, which keeps the bootstrap prompt active longer than intended.

## 新人易踩的坑

The `run()` method's Step 1-1 comment says "Event selection disabled" and sets `messages = []`. This is not a bug — it is a documented transitional state. Do not "fix" it by restoring `extract_narrative_data()` without understanding that `ChatModule.hook_data_gathering()` in Step 1-2 is now the authoritative source of conversation history. Enabling both simultaneously would produce duplicate message history.

`ContextRuntime.__init__()` accepts a `database_client` parameter but falls back to `get_db_client_sync()` if none is provided. In test environments where no database is available, omitting this parameter produces a `DatabaseClient` that fails on the first `await` rather than at construction time — the same lazy-init gotcha documented in `database.py`.
