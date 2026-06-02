---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_4_persist_results.py
last_verified: 2026-05-29
stub: false
---

## 2026-05-20 (Fix #2 P3) — 4.0 narrative routing signal

New step 4.0 honors the agent's switch_narrative / create_narrative tool calls
(basic_info MCP — see [[basic_info_module.py]]). `_detect_narrative_routing_signal`
scans `execution_result.agent_loop_response` for the LAST such call; on switch it
loads the target narrative, on create it makes one via
`narrative_service.create_narrative`. The target then becomes the head of
`ctx.narrative_list` (note: `ctx.main_narrative` is a read-only property over
`narrative_list[0]`, so we override the list, not the property) + the local
`main_narrative`, so the event attribution (4.4), markdown stats (4.2), summary
updates, and the session anchor (4.5) all flow to it; `session.current_narrative_id`
is repointed so the NEXT turn continues there. It ALSO re-binds THIS turn's chat
persistence: step_5's ChatModule hook writes to the module object's
`self.instance_id` (bound in step_1 to the ORIGINAL narrative's chat instance),
so 4.0 calls `_ensure_user_chat_instance(target)` and resets every ChatModule in
`ctx.module_list` (`instance_id`/`instance_ids`) + `ctx.user_chat_instances[target]`
BEFORE step_5 runs — so the message lands in the thread it now belongs to, not
the original. (Rebind is best-effort/try-except: on failure the event is still
re-attributed and the message just stays in the original thread.)

## 2026-05-20 — 4.5 anchor on ANY user-visible delivery (not just human turns)

Supersedes the 2026-05-19 "仅在人-回复轮写 last_response" rule below. The session
continuity anchor must track the **last message visible in the user's chat
box** — which includes a message the agent DELIVERED to the user this turn even
from a background trigger (a scheduled job / heartbeat can call
`send_message_to_user_directly`; from the user's POV that's the latest
interaction). New module-level `_turn_delivered_user_message(agent_loop_response,
working_source)` reuses the `MessageSourceRegistry` reply-tool detection (no
chat_module import — modules stay hot-pluggable, 铁律 #3). Anchor condition is
now `is_user_chat OR delivered_user_message`. For a proactive (non-human)
delivery, Step 1 skipped the anchor, so step_4 sets `current_narrative_id`
= this turn's narrative, clears `last_query`/`last_query_embedding` (no prior
user query) and sets `last_query_time=now`; `last_response` = the delivered
message. Pure machine traffic (a job/bus turn that did NOT message the user)
still leaves the anchor untouched. See [[narrative_service.py]] /
[[session_service.py]] 2026-05-20.

## 2026-05-19 — 4.5 仅在「人-回复轮」写 `last_response`

Background trigger runs (`JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY`)
不再覆盖 `session.last_response`，跟 Step 1 的 `last_query` 守护对齐。
判断口径统一走 [[hook_schema.py]] 的 `WorkingSource.is_from_human()` —
CHAT / LARK / SLACK / TELEGRAM 是 True；上面 4 个是 False。`working_source`
为 None / 未知字符串时默认按 human 处理（legacy safe default）。这是
short-reply 连续性崩 bug 修复的另一半（详见 [[narrative_service.py]]
2026-05-19 段）。

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
