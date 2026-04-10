---
code_file: src/xyz_agent_context/schema/decision_schema.py
last_verified: 2026-04-10
stub: false
---

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
