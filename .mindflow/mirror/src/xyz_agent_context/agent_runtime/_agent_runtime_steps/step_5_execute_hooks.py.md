---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_5_execute_hooks.py
last_verified: 2026-07-02
stub: false
---

## 2026-07-02 — `build_after_execution_params` default is CHAT-shape

Structural change to `build_after_execution_params`: the special-case
branch for `WorkingSource.JOB` is unchanged, but everything else
(`CHAT`, `LARK`, `SLACK`, `TELEGRAM`, `DISCORD`, `NARRAMESSENGER`,
`WECHAT`, `MESSAGE_BUS`, `A2A`, ...) now runs the CHAT resolution
logic — user_chat_instances by narrative_id → fallback to
`provides_chat_history()==True` — rather than falling through to
`current_instance = None` with a warning.

Why. All non-JOB working_sources are conversation-shaped and route
through ChatModule for the turn's chat history; the "current
instance" for hook purposes is naturally the ChatModule instance
that carries that narrative's history. The old CHAT-only branch was
a pre-multi-IM leftover — LARK / SLACK / etc. hit `None` and logged
"No instance found for working_source=..." on every turn.

Impact today. Zero user-visible behaviour change:

- `_job_lifecycle.py` is the only site that reads `params.instance`,
  and it only fires on `working_source=JOB`, which still takes the
  JOB branch.
- `ChatModule.hook_persist_turn` uses `self.instance_id` (bound
  during step_2 module load), not `params.instance` — chat history
  writes were never dependent on this field.
- The change eliminates a persistent per-turn WARN on IM channels.
- Future hooks that legitimately want "which chat instance is this
  turn about" now get the right answer for all IM channels for free.


## 2026-05-20 — extracted `build_after_execution_params(ctx)`

The current-instance resolution + HookAfterExecutionParams construction was lifted
out of `step_5_execute_hooks` into a module-level `build_after_execution_params(ctx)`
so the new SYNCHRONOUS persistence phase ([[agent_runtime.py]] Step 4.6 →
[[hook_manager.py]] `hook_persist_turn`) builds identical params without duplicating
the resolution logic. Step 5 (background) now just calls it. Pure read over ctx.

# step_5_execute_hooks.py — Pipeline Step 5: Execute Module Post-turn Hooks

## Why It Exists

After the turn is persisted (Step 4), each active Module gets a chance to run its `hook_after_event_execution` callback. These hooks handle Module-specific post-processing: saving chat messages to ChatModule, triggering Job scheduling in JobModule, updating social graph data, etc. Running hooks after persistence ensures they operate on committed data and don't block the WebSocket response.

## Upstream / Downstream

**Called by:** `agent_runtime.py` — dispatched as a background `asyncio.Task` after Step 4 completes, so the WebSocket can close while hooks run

**Calls:**
- `hook_manager.run_hooks()` — iterates all active Module instances and calls `hook_after_event_execution` on each
- Each Module's `hook_after_event_execution(params: HookAfterExecutionParams)` implementation

**Produces:**
- `callback_results` dict — returned via the final `yield` in the generator; collected by the background task wrapper in `agent_runtime.py`
- Side effects in DB (per-Module)

## Key Design Decisions

### Dispatched as Background Task
Steps 5 and 6 are pushed to `asyncio.create_task()` after Step 4. This means the WebSocket connection can close and the HTTP response can be sent while hooks run in the background. The client does not wait for hooks.

This is intentional: hooks can be slow (e.g., JobModule scheduling requires LLM calls). Blocking the user's response on hook completion would degrade perceived latency.

### current_instance Resolution by working_source
The `current_instance` parameter passed to `HookAfterExecutionParams` is determined differently based on `ctx.working_source`:
- **CHAT**: uses `ctx.user_chat_instances[narrative_id]` — the per-user ChatModule instance established in Step 1
- **JOB**: uses `ctx.job_instance_id` — the specific JobModule instance that triggered the turn
- **Other**: falls back to `None`

This distinction matters because hooks need to know "which instance owns this turn's data" to route their writes correctly.

### callback_results Return via Final Yield
The generator's final `yield` carries `callback_results` as the `details` field of the last `ProgressMessage`. The background task wrapper in `agent_runtime.py` collects this via `async for` and stores it on `ctx`. This is an unusual pattern — it's how the background task communicates results back without a shared mutable reference.

## ContextData Mutations

| Field | What Happens |
|-------|-------------|
| `ctx.callback_results` | Set by the background task wrapper after this generator completes |
| Module-specific DB tables | Each Module's hook writes its own data (chat_messages, job_runs, etc.) |

## Gotchas / Edge Cases

- **Hook failure isolation**: Each hook runs in a try/except. One Module's hook failure does not prevent other Modules' hooks from running. Errors are logged and added to `callback_results` with an error status.
- **Background task lifecycle**: Since this runs as a background task, it may outlive the HTTP request. Do not hold references to request-scoped objects (e.g., WebSocket connection) inside hooks.
- **Ordering not guaranteed**: Hooks run in iteration order of `ctx.active_instances`. If Hook A depends on Hook B's side effects, this is a design smell — hooks should be independent.
- **No cancellation token**: Background tasks (Steps 5–6) do not receive the `CancellationToken`. If the user cancels the turn, hooks still run to completion on the already-persisted data.

## Common New-Developer Mistakes

- Expecting hook results to be available before the WebSocket response: they're in a background task, so the client may not see them until the next turn.
- Adding slow synchronous operations to a hook: all hooks must be async. Blocking the event loop in a hook will delay all other background tasks.
- Forgetting that `ctx.user_chat_instances` may not have an entry for every Narrative — always use `.get()` with a fallback, not direct key access.
