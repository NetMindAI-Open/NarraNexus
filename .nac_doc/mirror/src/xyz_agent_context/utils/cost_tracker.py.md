# cost_tracker.py

LLM API cost calculation and recording — a contextvars-based ambient context that lets any LLM call record its cost without passing `agent_id` and `db` explicitly.

## Why it exists

The agent runtime calls multiple LLM APIs (Claude via the Anthropic SDK, OpenAI GPT, Gemini, OpenAI Embeddings) in a single turn. Recording the cost of each call required either threading `agent_id` and `db` through every function that eventually calls an LLM, or using a global mutable variable. `cost_tracker.py` uses Python's `contextvars.ContextVar` to store `(agent_id, db_client)` as an ambient context tied to the current async task. `AgentRuntime.run()` sets this context once at the start of a turn; all LLM callers downstream can then call `record_cost()` without extra parameters.

## Upstream / Downstream

**Set by:** `agent_runtime/` — `AgentRuntime.run()` calls `set_cost_context(agent_id, db)` at the start and `clear_cost_context()` in the `finally` block.

**Called by:** `agent_framework/llm_api/` (Claude SDK wrapper, OpenAI wrapper, Gemini wrapper, embedding client) — each records its token usage via `record_cost()` after a successful API call.

**Reads from:** `MODEL_PRICING` dict for per-million-token USD rates (GPT, Gemini, embeddings). Claude costs use the SDK's reported `cost_usd` directly.

**Writes to:** the `cost_records` table via the ambient `db_client`.

## Design decisions

**`contextvars.ContextVar` for asyncio safety.** Unlike a module-level global, a `ContextVar` is scoped to the current async task and its children. Concurrent agent runs on different asyncio tasks each see their own `(agent_id, db)` pair without interfering.

**`AgentRuntime.run()` owns the lifecycle.** The cost context is set exactly once per agent turn and cleared in `finally`. This means any function called during the turn can call `get_cost_context()` and get valid data, but functions called outside a turn (e.g., admin scripts) will see `None` and should handle that gracefully.

**`MODEL_PRICING` covers only models controlled by our code.** Only OpenAI GPT (hardcoded in the OpenAI SDK wrapper), Gemini (hardcoded in the Gemini SDK wrapper), and embedding models (from `settings.openai_embedding_model`) are listed. Claude costs come from the SDK's own cost reporting rather than a price table, so no Claude entry is needed.

**`calculate_cost` is a pure function.** It takes model name and token counts and returns a USD amount. It is separate from `record_cost` so that callers can estimate cost before committing to a database write, and so that tests can verify cost calculations without a database.

## Gotchas

**`get_cost_context()` returns `None` outside of `AgentRuntime.run()`.** Any LLM call made outside the agent runtime (e.g., in a standalone script or a test that calls an LLM function directly) will get `None` from `get_cost_context()`. The recording step will be silently skipped rather than raising an error. This is intentional — cost tracking should never block the actual LLM call.

**`ContextVar` does not propagate to `asyncio.create_task()` by default in Python < 3.7.1.** In modern Python (3.7.1+), `create_task` copies the current context, so child tasks see the parent's cost context. This is the expected behavior, but be aware that manually creating a new `Context` object and running a coroutine in it will lose the cost context.

**New-contributor trap.** The `MODEL_PRICING` dict uses the exact model name strings that our SDK wrappers pass. If a model name changes (e.g., a new GPT version), the pricing entry must be updated by the same name string. A model name mismatch causes `calculate_cost` to return 0.0 silently, which means the cost is not recorded but no error is raised.
