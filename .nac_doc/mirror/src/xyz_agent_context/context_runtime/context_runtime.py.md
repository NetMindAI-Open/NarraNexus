---
code_file: src/xyz_agent_context/context_runtime/context_runtime.py
last_verified: 2026-04-10
stub: false
---

# context_runtime.py — the assembly engine that turns raw Narrative + Module state into a ready-to-submit LLM payload

## 为什么存在

Before each LLM call, the agent needs a fully formed system prompt and a message list. That assembly is non-trivial: it requires pulling the right Narrative summary, firing every active module's data-gathering hook, sorting module instructions by priority, routing conversation history into two memory tracks (long-term vs. short-term), truncating oversized messages, and collecting MCP server URLs for tool access — all in a deterministic order. `ContextRuntime` owns that entire assembly pipeline so the orchestration layer (`step_3_agent_loop.py`) can hand it a Narrative list and an instance list and receive back a `ContextRuntimeOutput` without knowing anything about how the prompt was built.

Without this class, the assembly logic would bleed into `AgentRuntime` steps, each module would need to know about every other module's output format, and the prompt structure would become impossible to reason about or test in isolation.

## 上下游关系

**Receives from:**
- `step_3_agent_loop.py` (inside `agent_runtime/_agent_runtime_steps/`) is the exclusive runtime caller. It constructs a `ContextRuntime` instance with the `agent_id`, `user_id`, and a `DatabaseClient`, then calls `.run()` with the Narrative list and active module instances produced by earlier pipeline steps.
- `NarrativeService` (`narrative/`) — called inside `build_complete_system_prompt()` to format the main Narrative's summary prompt via `combine_main_narrative_prompt()`.
- `HookManager` (`module/hook_manager.py`) — invoked in `run()` Step 1-2 to fire `hook_data_gathering` on every loaded module, which allows modules like `ChatModule` to populate `ctx_data.chat_history`.
- `AgentRepository` (`repository/`) — queried directly inside the Bootstrap injection block to look up who created the agent, bypassing `BasicInfoModule` to avoid a module-load dependency.
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
