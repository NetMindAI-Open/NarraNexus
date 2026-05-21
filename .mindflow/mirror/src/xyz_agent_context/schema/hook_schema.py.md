---
code_file: src/xyz_agent_context/schema/hook_schema.py
last_verified: 2026-05-19
stub: false
---

## 2026-05-19 — `WorkingSource.is_from_human()` 新方法

跟 `is_automated()` 和 `is_user_initiated()` **并存** (不动旧 API)。语义是
"对方是不是真人，需不需要走 warm 风格"：

| value | is_from_human |
|---|---|
| CHAT / LARK / SLACK / TELEGRAM | **True** — 终点是真人，需要 warm 回复 |
| JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY | **False** — 终点是 agent / 后台，简洁优先 |

为什么不直接用 `is_user_initiated()`：那个只把 CHAT 当 user，但
LARK/SLACK/TELEGRAM 实际上都是真人在另一个 IM 通道说话，不应该按
agent 待遇冷处理。`is_automated()` 又把 IM 当 automated，方向也不对。
所以这个新 method 是专门给「人 vs 机器」这个语义维度用的。

**消费点**：
- `_agent_runtime_steps/step_1_select_narrative.py::_is_user_chat()` 用它判断
  是否更新 Session.last_query / current_narrative_id (避免被 cron / bus 污染)
- `_agent_runtime_steps/step_4_persist_results.py` 4.5 段同样判断是否写
  Session.last_response
- `module/chat_module/prompts.py` Guidelines 里把 warm-with-humans 规则的
  适用范围按这个 method 列出
- `module/message_bus_module/message_bus_module.py` 的 Reply Discipline
  在 `working_source=MESSAGE_BUS` 触发时强制 brevity + `[NO_REPLY]` 纪律

新增 source 类型时记得回来看这个 method —— default 不写就会按 enum 不在 4
个 False 集合里被归入 human，对新通道一般是对的（safe default），但如果是
新的 agent-to-agent 协议，必须把它加进 False 集合。

# hook_schema.py

## Why it exists

Every module in the system has a `hook_after_event_execution()` callback that fires after the agent finishes a turn. Originally this hook received a pile of `**kwargs` which made it impossible to know what was actually available without reading the caller. This file replaces those kwargs with typed dataclasses: callers construct a `HookAfterExecutionParams` and modules destructure it in a type-safe way.

`WorkingSource` is also defined here — the enum that identifies what kind of execution triggered the current turn (chat, job, a2a, callback, etc.).

## Upstream / Downstream

`AgentRuntime` (Step 8) constructs `HookAfterExecutionParams` from the `PathExecutionResult` and fires `HookManager.hook_after_event_execution()`. Every module's hook implementation receives a single `HookAfterExecutionParams` argument. `WorkingSource` is imported by `context_schema.py` (`ContextData.working_source`) and by the narrative system to decide how to update summaries differently for chat vs job executions.

## Design decisions

**Three nested dataclasses (`HookExecutionContext`, `HookIOData`, `HookExecutionTrace`) instead of one flat dataclass**: this grouping reflects what different kinds of modules need. A lightweight module might only need `execution_ctx` (who/where/what). A heavy analysis module like `JobModule` additionally needs `trace` (the raw agent loop response to parse tool calls). The nesting means a module can assert `if params.trace is None: return` and skip expensive processing entirely.

**`HookAfterExecutionParams.event` and `narrative` fields**: these were added specifically for EverMemOS-style memory writing that needs the live Narrative and Event objects (not just their IDs). Rather than adding another layer of nesting, they sit directly on the params struct.

**Convenience properties on `HookAfterExecutionParams`**: `params.event_id`, `params.final_output`, `params.event_log` etc. are pass-through properties that flatten the nesting for the common case. The nesting is there for type clarity but should not force every module to write `params.execution_ctx.event_id`.

**`WorkingSource` inherits from `str`** so it compares equal to its string value in legacy code paths that still use raw strings. This was a deliberate bridge choice during migration.

## Gotchas

**`HookExecutionTrace` is `Optional` in `HookAfterExecutionParams`**. For `DIRECT_TRIGGER` executions, `trace.agent_loop_response` is always an empty list and may not be set at all. Any module that accesses `params.agent_loop_response` without checking for `None` first will get an empty list via the property (safe), but direct attribute access via `params.trace.event_log` will raise `AttributeError` if `trace` is `None`.

**`WorkingSource.MESSAGE_BUS`** is not yet wired to a concrete trigger implementation. It exists as a reservation. If you see `working_source == "message_bus"` in production data, something set it explicitly and there is no standard handler for it yet.

## New-joiner traps

- `WorkingSource.is_automated()` includes `MATRIX` and `MESSAGE_BUS`. This means Matrix messages are not treated as "user-initiated" even though a human sent them. The distinction matters for Narrative summary strategies — automated executions generate briefer summaries by default.
- Do not confuse `HookAfterExecutionParams.instance` (the `ModuleInstance` that is currently executing) with `ctx_data.extra_data.get("job_id")` or similar module-specific context. The `instance` field is the generic module instance; module-specific state must be retrieved from `ctx_data.extra_data`.
