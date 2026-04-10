---
code_dir: src/xyz_agent_context/context_runtime/
last_verified: 2026-04-10
stub: false
---

# context_runtime/ — the prompt-assembly engine that sits between Narrative memory and the LLM call

## 目录角色

`context_runtime/` is a focused two-file package. Its sole responsibility is to take everything the agent knows — the active Narrative's summary, auxiliary Narrative hints, module-specific instructions, the dual-track conversation history produced by `ChatModule`, and optional bootstrap directives — and assemble it into the exact `(messages, mcp_urls)` payload that the agent framework adapter expects before every LLM invocation.

The directory has no storage, no background tasks, and no MCP server. It is a pure transformation step: receives structured data from `narrative/` and `module/`, emits a fully-formed LLM input. This boundary is deliberate — it keeps the orchestration layer (`agent_runtime/`) decoupled from the specifics of prompt construction.

## 关键文件索引

| File | Role |
|---|---|
| `context_runtime.py` | `ContextRuntime` class — the 5-step assembly pipeline (`run()` → data gathering → instruction build → system prompt build → framework input build) |
| `prompts.py` | Read-only vocabulary: the four static string constants (`AUXILIARY_NARRATIVES_HEADER`, `MODULE_INSTRUCTIONS_HEADER`, `SHORT_TERM_MEMORY_HEADER`, `BOOTSTRAP_INJECTION_PROMPT`) that label every structural section of the assembled prompt |
| `__init__.py` | Re-exports `ContextRuntime` as the package's public interface |

## 和外部目录的协作

- **`agent_runtime/_agent_runtime_steps/step_3_agent_loop.py`** — the only production caller. It constructs `ContextRuntime(agent_id, user_id, db_client)` and calls `.run(narrative_list, active_instances, input_content, ...)`, then passes the returned `ContextRuntimeOutput` forward to the framework adapter step.
- **`narrative/`** — `NarrativeService` is called by `ContextRuntime` to render the main Narrative's summary into a prompt string. The list of `Narrative` objects itself arrives from `agent_runtime` (produced by the Narrative selection step that runs before `step_3`).
- **`module/`** — `HookManager` is used to fire `hook_data_gathering` on every active module instance, allowing modules to inject data (most importantly `ctx_data.chat_history` from `ChatModule`) before the prompt is assembled. Module MCP URLs are also collected here.
- **`schema/`** — `ContextData`, `ModuleInstructions`, `ContextRuntimeOutput`, and `WorkingSource` are the typed containers that flow through the pipeline. `ContextRuntime` creates a fresh `ContextData` at the start of each `run()` call and returns it inside `ContextRuntimeOutput`.
- **`repository/`** — `AgentRepository` is imported directly inside the Bootstrap injection block to look up the agent's creator without depending on `BasicInfoModule` being loaded.
- **`prompts_index.py`** (package root) — re-exports the constants from `prompts.py` as a convenience alias so callers outside `context_runtime/` can reference prompt wording without depending on the sub-package path.
