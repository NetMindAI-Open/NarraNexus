---
code_file: src/xyz_agent_context/schema/decision_schema.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — `PathExecutionResult.interrupted`

打断连续性的载体字段:用户 Stop 的 turn 不再被丢弃,部分结果照常持久化,消费方
(hook 参数、ChatModule persist)靠它区分「被掐断」与「自然结束」。

## 2026-07-29 (二次) — 删掉三个句柄校验伴随字段

`cli_framework` / `cli_config_fingerprint` / `cli_working_path` 删除。它们唯一的
用途是校验一个**存下来的**句柄(框架要匹配、配置指纹要匹配、工作路径要匹配),
而句柄机制整体已删。

`cli_session_id` **保留但降级为纯观测值**:仍从 `ResultMessage.session_id` 采集、
仍进日志,但没有任何消费者。留着是因为它是"某一轮 CLI 报了哪个会话"的唯一记录,
排查 resume 行为时有用。注释已改写,免得下一个读者以为它还会被写进库。

## 2026-07-29 — 删除 PathExecutionResult.resume_failed(T5)

随 [[execution_state]] 同名字段一起删:它只用于把"句柄过期"从 step_3 传到 step_4,
而 step_4 的句柄持久化已不存在。

## 2026-07-28 — PathExecutionResult 加 resume_failed（resume 化 R3）

`resume_failed: bool = False`——step_3 从 state 无条件透传（即便重试没报新
cli_session_id 也要透传，step_4 靠它删陈旧句柄）。内部信号，永不面向用户。
计划里提过的 `resumed` 观测字段**没有加**：观测走 cost_records 的 cache 两列
推断（V-j），不占 schema。

## 2026-07-25 — PathExecutionResult 加 CLI 句柄四字段(resume 化 R1)

`cli_session_id` / `cli_framework` / `cli_config_fingerprint` / `cli_working_path`
(全部默认 None)。step_3 只在 state.cli_session_id 非空时填伴随三项(指纹
fail-open 可为 None);step_4 据此 upsert `agent_cli_sessions`。`cli_framework`
之所以在这里搭车:step_4 拿不到 step_3 的 framework_name 局部(ctx 不携带),
per-run 数据走 PathExecutionResult 是既定通道。DIRECT_TRIGGER / 非 Claude 路径
保持默认 None。

## 2026-07-23 — PathExecutionResult 加 cache/num_turns 三字段(W1)

`cache_read_tokens`/`cache_creation_tokens`(默认 0)+ `num_turns`(默认 None)。
只是 ExecutionState → step_4 的搬运位,语义见 execution_state.py.md 同日条目。
DIRECT_TRIGGER 路径不填,保持默认值。

# decision_schema.py

## Why it exists

This file defines the data contracts for Step 2 of the AgentRuntime pipeline — the "Approach 2" intelligent decision layer. After modules are loaded, Step 2 asks the LLM to decide two things: which module instances should be active for this turn, and whether execution should go through the full Agent Loop (complex reasoning) or short-circuit to a Direct Trigger (simple deterministic action). `ModuleLoadResult` is the envelope carrying that decision forward to Step 3.

`PathExecutionResult` is the unified output produced by whichever execution path runs, ensuring Steps 7 and 8 (event update, hook execution) can operate identically regardless of which path was taken.

## Upstream / Downstream

`ModuleService.load_modules()` in `_module_impl/` returns a `ModuleLoadResult`. `AgentRuntime` Step 3 inspects `execution_type` to branch into either the agent loop or a direct trigger call. The resulting `PathExecutionResult` flows into `AgentRuntime` Step 7 (event finalization) and Step 8 (hook execution).

`DirectTriggerConfig` is consumed by the direct trigger execution path — it tells the runtime exactly which module class, trigger name, and parameters to invoke without LLM reasoning.

## Design decisions

**`ExecutionPath` is a regular `Enum`, not `str, Enum`**: this is intentional. It never needs to be serialized to a string in a database or JSON response; it is purely an in-memory routing signal. Using a plain Enum makes it impossible to accidentally compare against string literals.

**`ModuleLoadResult.llm_error`**: if the LLM decision call in Step 2 fails, the system falls back to a safe default (e.g., keep existing instances, choose AGENT_LOOP) and records the error in `llm_error`. Step 2 surfaces this to the frontend so users know the decision was degraded. The decision was to never let an LLM failure block execution — degrade gracefully and log.

**`changes_summary` and `changes_explanation` are separate fields**: `changes_summary` is a simple dict of added/removed/kept lists for fast structural inspection. `changes_explanation` is the raw LLM output explaining its reasoning. Separating them prevents code that just wants to know "was anything added?" from having to parse a narrative string.

**`raw_instances`** carries the full InstanceDict list including `job_config` that is needed specifically for Job creation. This was added later to avoid a second database lookup in the Job creation flow.

## Gotchas

**`ModuleLoadResult.execution_type` defaults to `None`** (the field says `default=None` but the type annotation says `ExecutionPath`). If Step 2 fails completely and no fallback sets the field, accessing `execution_type` returns `None`. Step 3 must handle `None` — treat it as `AGENT_LOOP`.

**`PathExecutionResult.ctx_data` is `Optional[Any]`** (annotated as `Any` to avoid circular imports). At runtime it will be a `ContextData` instance, but type checkers cannot verify this. Any code consuming `ctx_data` from a `PathExecutionResult` must cast or accept the `Any` type.

## New-joiner traps

- `ModuleLoadResult.active_instances` contains `ModuleInstance` objects (from `instance_schema.py`) with the runtime `module` field bound, but they are typed as `List[Any]` here to avoid circular imports. Do not mistake this for a list of raw `ModuleInstanceRecord` database records — these have live Python module objects attached.
- `key_to_id` maps a "task key" (a short label the LLM assigns to a work unit) to an `instance_id`. This is only relevant when complex Job orchestration is in play; for normal chat it is always empty.
