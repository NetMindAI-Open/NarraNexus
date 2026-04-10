---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_4_persist_results.py
last_verified: 2026-04-10
stub: false
---
# step_4_persist_results.py — Pipeline Step 4: Persist Turn Results

## Why It Exists

After the LLM turn completes (Step 3), all results must be durably written to the database before the WebSocket connection closes. This step is the "commit point" of a turn: Trajectory records, Narrative summaries, Event status updates, Session state, and cost accounting all happen here. Steps 5–6 (hooks) run as background tasks after this step completes.

## Upstream / Downstream

**Called by:** `agent_runtime.py` — Step 4 in the 7-step pipeline

**Reads from ctx:**
- `ctx.execution_result` — the `PathExecutionResult` from Step 3
- `ctx.narrative_list`, `ctx.active_instances` — for Narrative update logic
- `ctx.event` — updated with final status
- `ctx.session` — updated with last-active timestamp

**Writes to DB (6 sub-steps):**
1. **Trajectory** — full turn record (input, output, tool calls, token usage)
2. **Markdown stats** — updates Module instance Markdown with turn statistics
3. **Event update** — marks Event as completed/failed with result summary
4. **Narrative update** — updates narrative summary and typing (default/main/auxiliary)
5. **Session** — saves updated session state
6. **Cost recording** — records LLM token costs to `agent_cost_log` table

## Key Design Decisions

### Narrative Typing Logic
Each Narrative in `ctx.narrative_list` gets typed as `default`, `main`, or `auxiliary` based on its role in the turn:
- **default**: the first Narrative (index 0) if no explicit main was selected
- **main**: the Narrative that received the primary LLM output
- **auxiliary**: all other Narratives consulted during context building

This typing is persisted so that future turns can prioritize the main Narrative in search.

### Event Final State
The Event record (created in Step 0) is updated here with: final status (`completed`/`failed`/`cancelled`), response summary, token counts, and duration. Downstream analytics and Job scheduling depend on Event records being consistently closed.

### Cost Recording Deferred to Step 4
Although token usage is tracked throughout the turn in `ExecutionState`, the final cost record is written here (not in Step 3) because it requires the final accumulated totals from `accumulate_usage()`. Writing partial costs mid-turn would create duplicates.

### Sub-step Granularity
Each of the 6 sub-steps yields a `ProgressMessage`. This gives the frontend visibility into which persistence operation is slow (e.g., a slow Narrative embedding update), which is useful for debugging production latency.

## ContextData Mutations

Step 4 does not mutate `RunContext` fields — it reads and writes to the database. However, `ctx.event.status` is updated in-memory as a side effect (to reflect the final state before saving).

## Gotchas / Edge Cases

- **Narrative update order matters**: Narrative embedding must be updated before Markdown stats, because the embedding depends on the current narrative summary which may have just been updated.
- **Failed turns still persist**: Even if Step 3 raised an exception, Step 4 runs (in a `finally` block in `agent_runtime.py`) to record the failed Event and any partial trajectory data. Do not assume `ctx.execution_result` is always fully populated.
- **Cost recording is non-fatal**: If the cost insert fails (e.g., DB constraint), the error is logged but does not raise. A missing cost record is better than a failed turn.

## Common New-Developer Mistakes

- Adding new DB writes after Step 4 in the main pipeline: anything that needs to be durable before the WebSocket closes must go here. Steps 5–6 run as background tasks after the socket closes.
- Assuming `ctx.narrative_list[0]` is always the "main" Narrative: main is determined by the LLM's selection logic in Step 3, not by list position.
- Forgetting to handle the case where `ctx.execution_result` is `None` (cancelled turn) — all sub-steps must guard for this.
