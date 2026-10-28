"""
@file_name: step_4_persist_results.py
@author: NetMind.AI
@date: 2025-12-24
@description: Step 4 - Persist execution results

Merged the original step_4, step_3_5, step_3_6 for unified result persistence:
- Record Trajectory (execution trace)
- Update Markdown statistics
- Update Event and Narratives (database persistence)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator, TYPE_CHECKING

from loguru import logger
from narranexus.platform.utils.logging import timed

from narranexus.platform.schema import ProgressMessage, ProgressStatus
from narranexus.platform.narrative import EventLogEntry
from narranexus.platform.agent_runtime.execution_state import ExecutionState
from narranexus.platform.utils.cost_tracker import record_cost
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.timezone import DEFAULT_TIMEZONE

if TYPE_CHECKING:
    from .context import RunContext
    from narranexus.platform.narrative import (
        EventService,
        NarrativeService,
        NarrativeMarkdownManager,
        TrajectoryRecorder,
        SessionService,
    )


def _owner_visible_reply_texts(agent_loop_response, working_source: str) -> list:
    """Every piece of text the OWNER actually saw this turn, in order.

    THE single traversal for "what did the owner see" in this file. Two
    consumers read it: the session anchor asks only whether the list is
    non-empty, the temporal guard reads the texts themselves. Keeping one
    implementation is what stops them from ever disagreeing — and a
    disagreement here is nasty to find, because the symptom is an audit
    probe silently skipping part of the traffic while its numbers look fine.

    Owner-visible specifically: the owner-facing tool this source's desk
    carries — ``reply_owner`` on owner chat, ``notify_owner`` everywhere else,
    plus the per-channel reply tools like ``lark_cli`` for IM sources. A bus
    reply to a peer agent is a delivery, but no human read it, so it must not
    re-anchor the owner's session (see
    MessageSourceHandler.owner_visible_reply_tool_names) and it is not
    something to measure human-facing accuracy against.

    Imports the channel registry (not any concrete Module) on purpose —
    Modules stay hot-pluggable (铁律 #3); the registry is shared infra.
    On any shape/registry mismatch we return `[]`, which reads as "nothing
    delivered": the background-delivery anchor is skipped and the guard runs
    on nothing. The human-turn anchor path is unaffected either way.
    """
    texts: list = []
    try:
        from narranexus.platform.schema import ProgressMessage
        from narranexus.platform.channel.message_source_handler import (
            MessageSourceRegistry,
        )

        handler = MessageSourceRegistry.get(working_source)
        for resp in agent_loop_response or []:
            if not (isinstance(resp, ProgressMessage) and resp.details):
                continue
            text = handler.extract_owner_visible_text(
                resp.details.get("tool_name", ""),
                resp.details.get("arguments", {}),
            )
            # Falsy covers both "not a reply tool" (None) and "reply that
            # stripped to blank" ("") — same two cases the boolean predicate
            # below has always treated as "nothing delivered".
            if text:
                texts.append(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"_owner_visible_reply_texts: detection failed ({e}); "
            "treating as not delivered"
        )
        return []
    return texts


def _turn_delivered_user_message(agent_loop_response, working_source: str) -> bool:
    """Did this turn deliver a user-visible message?

    True iff the agent fired a reply tool that surfaces in the user's chat
    (``send_message_to_user_directly`` for any source; plus the per-channel
    reply tools like ``lark_cli`` for IM sources).

    Value-identical to the hand-rolled loop this used to be — that loop
    returned True on the first truthy `extract_owner_visible_text` and False
    otherwise, which is exactly `bool()` of the list above — with ONE
    documented exception, so nobody reads the equivalence as unconditional:

    the old loop short-circuited on the first hit and so never touched later
    elements. The list version walks the whole response, so a malformed
    element AFTER a real reply now sends the traversal down its `except` and
    flips this predicate from True to False. That is deliberate: "we could
    not read the response cleanly" resolves to "treat as not delivered",
    which is the safe side for both consumers (the anchor stays put, the
    guard skips a turn). Do NOT "improve" the except into `return texts` to
    salvage what was collected — that is exactly what would let the two
    readings diverge on a partially-broken response.

    Pinned by `test_boolean_predicate_agrees_with_the_list_on_every_case`,
    including the hit-then-malformed input.
    """
    return bool(_owner_visible_reply_texts(agent_loop_response, working_source))


def _detect_narrative_routing_signal(agent_loop_response):
    """Scan the agent-loop response for a switch_narrative / create_narrative
    call (basic_info MCP tools, Fix #2 P3). Returns the LAST such (kind, args)
    where kind in {'switch','create'} and args is the tool-call arguments, or
    None. These tools are signals — the runtime does the actual re-attribution
    (see the 4.0 block in step_4). Keep the tool names in lockstep with
    basic_info_module._basic_info_mcp_tools.
    """
    found = None
    try:
        from narranexus.platform.schema import ProgressMessage
        for resp in agent_loop_response or []:
            if not (isinstance(resp, ProgressMessage) and resp.details):
                continue
            tn = resp.details.get("tool_name", "") or ""
            args = resp.details.get("arguments", {}) or {}
            if tn.endswith("switch_narrative"):
                found = ("switch", args)
            elif tn.endswith("create_narrative"):
                found = ("create", args)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_detect_narrative_routing_signal failed: {e}")
    return found


@timed("step.4_persist_results")

async def step_4_persist_results(
    ctx: "RunContext",
    event_service: "EventService",
    narrative_service: "NarrativeService",
    markdown_manager: "NarrativeMarkdownManager",
    trajectory_recorder: "TrajectoryRecorder",
    session_service: "SessionService"
) -> AsyncGenerator[ProgressMessage, None]:
    """
    Step 4: Persist execution results

    Save execution results to various storages:
    1. Record Trajectory (execution trace file)
    2. Update Markdown statistics
    3. Update Event and Narratives (database)

    Args:
        ctx: Run context
        event_service: Event service
        narrative_service: Narrative service
        markdown_manager: Markdown manager
        trajectory_recorder: Trajectory recorder
        session_service: Session service

    Yields:
        ProgressMessage: Progress messages
    """
    yield ProgressMessage(
        step="4",
        title="Persist Results",
        description="Save execution trace, update statistics, persist to database",
        status=ProgressStatus.RUNNING,
        substeps=ctx.substeps_4
    )

    main_narrative = ctx.main_narrative
    execution_result = ctx.execution_result
    load_result = ctx.load_result

    # Narrative-level bookkeeping. A bare turn (no thread) never enters 4.1 /
    # 4.2 / 4.4, so these have to carry a truthful "nothing to count" value
    # into the completion message rather than blow up on it.
    current_round = 0
    total_toolcalls = 0
    unique_narratives: list = []

    # A turn with no narrative is NOT a turn with nothing to save (D-10).
    # Ephemeral surfaces (voice/F28) and the "no durable topic + no anchor"
    # landing both finish here with `narrative_list == []`, and their reply,
    # their memory index and their token cost are all still real. Only "the
    # agent never produced a result" means there is genuinely nothing to
    # write; the narrative-level sections guard themselves below.
    if not execution_result:
        yield ProgressMessage(
            step="4",
            title="Persist Results",
            description="✗ No execution results to save",
            status=ProgressStatus.COMPLETED,
            substeps=ctx.substeps_4
        )
        return

    # --- narrative-level sections (4.0-4.2) -------------------------------
    # Skipped, not fatal, when the turn has no thread: routing signals,
    # trajectory files and markdown stats are all ABOUT a narrative. The
    # event-level books below (4.3 write-back, 4.6 cost) are not, and used
    # to be lost with them — see the module docstring.
    if main_narrative is not None:
        # =========================================================================
        # 4.0 Narrative routing signal (Fix #2 P3)
        #
        # The agent may have used switch_narrative / create_narrative (basic_info
        # MCP tools) to say "this turn actually belongs to thread X" / "...to a NEW
        # thread". Those tools are signals; we do the re-attribution HERE:
        #   1. make the target the main narrative (narrative_list[0]) so the event
        #      (4.4), summary updates, markdown stats (4.2) and the session anchor
        #      (4.5) all flow to it, and point the session at it for the next turn;
        #   2. RE-BIND this turn's chat persistence to the target's chat instance —
        #      step_5's hook persists via the ChatModule object's `self.instance_id`,
        #      which was bound in step_1 to the ORIGINAL narrative's chat instance.
        #      We ensure/create the target's chat instance and reset the loaded
        #      module's instance_id BEFORE step_5 runs, so the message lands in the
        #      thread it now belongs to (not the original one).
        # =========================================================================
        routing = _detect_narrative_routing_signal(execution_result.agent_loop_response)
        if routing:
            kind, rargs = routing
            try:
                target = None
                if kind == "switch":
                    tnid = rargs.get("narrative_id")
                    if tnid:
                        target = await narrative_service.load_narrative_from_db(tnid)
                        if not target:
                            logger.warning(f"[NarrativeRouting] switch target {tnid} not found; keeping default")
                else:  # create
                    target = await narrative_service.create_narrative(
                        agent_id=ctx.agent_id,
                        user_id=ctx.user_id,
                        title=(rargs.get("title") or "New topic"),
                        description=(rargs.get("description") or ""),
                    )
                if target and target.id != main_narrative.id:
                    logger.info(
                        f"[NarrativeRouting] {kind} signal -> {target.id} "
                        f"(default was {main_narrative.id}); re-attributing this turn"
                    )
                    # main_narrative is a read-only property over narrative_list[0];
                    # override the list head (+ the local var used downstream).
                    if ctx.narrative_list:
                        ctx.narrative_list[0] = target
                    else:
                        ctx.narrative_list = [target]
                    main_narrative = target
                    if ctx.session:
                        ctx.session.current_narrative_id = target.id

                    # Re-bind THIS turn's chat persistence to the target thread.
                    # step_5's ChatModule hook writes to the module object's
                    # self.instance_id (bound in step_1 to the ORIGINAL narrative's
                    # chat instance). Ensure/create the target's chat instance and
                    # reset the loaded module(s) BEFORE step_5, so the message lands
                    # in the thread it now belongs to.
                    try:
                        from .step_1_select_narrative import _ensure_user_chat_instance
                        target_chat_id = await _ensure_user_chat_instance(
                            ctx.agent_id, ctx.user_id, target.id
                        )
                        rebound = 0
                        for m in (getattr(ctx, "module_list", None) or []):
                            if m.provides_chat_history():
                                m.instance_id = target_chat_id
                                m.instance_ids = [target_chat_id]
                                rebound += 1
                        if isinstance(getattr(ctx, "user_chat_instances", None), dict):
                            ctx.user_chat_instances[target.id] = target_chat_id
                        logger.info(
                            f"[NarrativeRouting] re-bound chat persistence -> instance "
                            f"{target_chat_id} ({rebound} ChatModule object(s))"
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"[NarrativeRouting] chat-instance rebind failed "
                            f"(message will stay in original thread): {e}"
                        )

                    ctx.substeps_4.append(f"[4.0] ✓ Narrative routing ({kind}) -> {target.id}")
                elif target:
                    logger.info(f"[NarrativeRouting] {kind} signal target == default; no change")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[NarrativeRouting] failed to apply {kind} signal: {e}")

        # =========================================================================
        # 4.1 Record Trajectory
        # =========================================================================
        # Round counter increment
        main_narrative.round_counter += 1
        current_round = main_narrative.round_counter

        # Construct ExecutionState
        temp_state = ExecutionState(
            final_output=execution_result.final_output,
            response_count=execution_result.response_count,
            tool_call_count=sum(
                1 for step in execution_result.execution_steps
                if step.get("type") == "tool_call"
            ),
            thinking_count=sum(
                1 for step in execution_result.execution_steps
                if step.get("type") == "thinking"
            ),
            all_steps=tuple(execution_result.execution_steps)
        )

        # Record trajectory
        await trajectory_recorder.record_round(
            narrative_id=main_narrative.id,
            round_num=current_round,
            user_input=ctx.input_content,
            instances=(
                load_result.active_instances
                if hasattr(load_result, 'active_instances')
                else []
            ),
            relationship_graph=(
                load_result.relationship_graph
                if hasattr(load_result, 'relationship_graph')
                else ""
            ),
            execution_state=temp_state,
            execution_path=ctx.execution_type.value,
            reasoning=(
                load_result.changes_explanation.get("reasoning", "")
                if hasattr(load_result, 'changes_explanation')
                else ""
            ),
            changes_summary=(
                load_result.changes_summary
                if hasattr(load_result, 'changes_summary')
                else {}
            ),
            previous_instances=ctx.previous_instances
        )

        ctx.substeps_4.append(f"[4.1] ✓ Trajectory recorded (Round {current_round})")
        logger.info(f"Trajectory recorded: Round {current_round}")

        # =========================================================================
        # 4.2 Update Markdown statistics
        # =========================================================================
        # Calculate statistics
        total_rounds = main_narrative.round_counter
        total_toolcalls = sum(
            1 for step in execution_result.execution_steps
            if step.get("type") == "tool_call"
        )

        # Calculate instance change count
        instance_changes = 0
        if hasattr(load_result, 'changes_summary') and load_result.changes_summary:
            instance_changes = (
                len(load_result.changes_summary.get("added", [])) +
                len(load_result.changes_summary.get("removed", [])) +
                len(load_result.changes_summary.get("updated", []))
            )

        # Get currently active instances
        active_instances = (
            load_result.active_instances
            if hasattr(load_result, 'active_instances')
            else []
        )

        # Most used Module
        module_usage = {}
        for inst in active_instances:
            module_class = inst.module_class
            module_usage[module_class] = module_usage.get(module_class, 0) + 1

        most_used_module = (
            max(module_usage.items(), key=lambda x: x[1])[0]
            if module_usage
            else "N/A"
        )

        # Update Markdown statistics
        await markdown_manager.update_statistics(
            narrative_id=main_narrative.id,
            stats={
                "total_rounds": total_rounds,
                "total_toolcalls": total_toolcalls,
                "instance_changes": instance_changes,
                "avg_active_instances": len(active_instances),
                "avg_toolcalls_per_round": total_toolcalls,
                "most_used_module": most_used_module
            }
        )

        ctx.substeps_4.append("[4.2] ✓ Markdown statistics updated")
        logger.debug("markdown statistics updated")

    # =========================================================================
    # 4.3 Update Event
    # =========================================================================
    # Build event log entries
    event_log_entries = []
    for step in execution_result.execution_steps:
        event_log_entries.append(EventLogEntry(
            timestamp=datetime.now(timezone.utc),
            type=step.get("type", "unknown"),
            content=step
        ))
    ctx.event_log_entries = event_log_entries
    ctx.module_instances = ctx.active_instances

    # Update Event
    await event_service.update_event_in_db(
        event_id=ctx.event.id,
        final_output=execution_result.final_output,
        event_log=event_log_entries,
        module_instances=ctx.module_instances,
    )

    # [IMPORTANT] Sync BOTH mutated fields back to the in-memory Event object.
    # Everything downstream of here (4.4 narrative update, step 5 hooks) reads
    # ctx.event, and it was created with `event_log=[]` — so until this sync it
    # told every consumer the turn ran no steps at all.
    #
    # Syncing only final_output has already cost us once: the 0802 "对话时序错乱"
    # report was phantom rows copied from this object carrying `event_log='[]'`
    # (see the 4.4 comment block below). It bites a second time at defect A1 —
    # the narrative updater reads event.event_log to get this turn's tool
    # actions into the retrieval surface, and a stale [] makes that extraction
    # a silent no-op in production while unit tests stay green.
    ctx.event.final_output = execution_result.final_output
    ctx.event.event_log = event_log_entries

    # Index this interaction (user input + agent reply) into the unified search
    # layer (memory_event) with a source_ref pointer back to the event — the
    # merged chat+event search index (design §5). `remember` finds past
    # exchanges; the pointer lets the agent fetch the full original via
    # view_event (or the recency-ordered conversation via get_chat_history).
    # Store comprehensively (user + reply); trim at use.
    try:
        from narranexus.platform.memory import MemoryEngine
        _interaction_text = "\n".join(
            p for p in [ctx.input_content or "", execution_result.final_output or ""] if p
        )
        if _interaction_text.strip():
            _db = await get_db_client()
            await MemoryEngine(_db, ctx.agent_id).index(
                "event", ctx.event.id, _interaction_text, scope_type="agent",
            )
    except Exception as e:  # noqa: BLE001 — index is best-effort enrichment
        logger.warning(f"interaction index failed (non-fatal): {e}")

    ctx.substeps_4.append(f"[4.3] ✓ Event updated: {ctx.event.id}")
    logger.info(f"Event updated: event_id={ctx.event.id}")

    # --- narrative-level section (4.4) ------------------------------------
    if main_narrative is not None:
        # =========================================================================
        # 4.4 Update Narratives
        #
        # A turn is AUTHORED into exactly one thread — the head of narrative_list
        # (post-4.0 routing). The rest of the list are the BM25 neighbours step_1
        # pulled in only to widen the read-side context (see
        # `MAX_NARRATIVES_IN_CONTEXT`); they get associated with the SAME event by
        # id. `narratives.event_ids` is a list, which is where that many-to-many
        # belongs, so a neighbouring thread can still replay this exchange via
        # `select_events_for_context`. `events.narrative_id` stays single-valued
        # and names the one thread the turn was authored into.
        #
        # This block used to write a COPY of the event row per auxiliary narrative
        # (`EventService.duplicate_event_for_narrative`, since deleted). The copy
        # was made here — at the END of the run — from the in-memory event, whose
        # `event_log` is never back-filled by 4.3, so every multi-narrative turn
        # left 1-2 rows carrying `state='completed'`, `started_at IS NULL`,
        # `tool_call_count=0`, `event_log='[]'` and the turn's `final_output`
        # (which is the agent's reasoning, not its reply). Replay surfaces that
        # read the `events` table then rendered the same exchange up to three
        # times, positioned by the run's FINISH time — an already-answered
        # question surfacing below newer ones, plus monologue fragments as
        # standalone entries. That is the 0802 "对话时序错乱" report.
        # =========================================================================
        await event_service.update_event_narrative_id(ctx.event.id, main_narrative.id)

        # One thread must be visited at most once. §4.0 sets narrative_list[0] to
        # the routing target, which may ALREADY sit at index 1-2 (the agent
        # switched to a thread that BM25 had also pulled in as a neighbour) — the
        # loop then hit that narrative twice. Before this change that produced a
        # second duplicate event row; it still appends a second identical
        # dynamic_summary entry, so the de-dup stays. Evidence it happens:
        # evt_f590aef867f14187 in the local DB carries the same narrative_id as
        # its own primary row.
        seen_narrative_ids: set[str] = set()
        unique_narratives = []
        for narrative in ctx.narrative_list:
            if narrative.id in seen_narrative_ids:
                logger.debug(f"narrative {narrative.id} listed twice this turn; visiting once")
                continue
            seen_narrative_ids.add(narrative.id)
            unique_narratives.append(narrative)

        for i, narrative in enumerate(unique_narratives):
            # Determine Narrative type
            # "Metadata-frozen" turns: file the event, touch nothing else.
            # The condition used to be "is this row a default bucket"; it is
            # now "does this TURN carry a durable topic" (C-1, plan 4-A').
            # Same branch, honest trigger — a greeting parked on the user's
            # active work thread must not spend a helper call rewriting that
            # thread's name/summary/keywords (NARRATIVE_LLM_UPDATE_INTERVAL is
            # 1, so every such turn would), because a "你好" is entirely
            # capable of renaming the work it interrupted.
            is_default = (
                narrative.is_special == "default"
                or getattr(ctx, "no_durable_topic", False)
            )
            is_main = (i == 0) and not is_default  # Metadata-frozen turns are not the main Narrative

            # Two freeze sources share one branch but must NOT share one
            # label: a legacy default-bucket row on a turn that DOES carry a
            # durable topic would otherwise be logged as "no_durable_topic" —
            # the one line of scene evidence a routing complaint gets.
            if is_default:
                update_type = (
                    "no_durable_topic"
                    if getattr(ctx, "no_durable_topic", False)
                    else "default_bucket"
                )
            elif is_main:
                update_type = "main"
            else:
                update_type = "auxiliary"

            logger.debug(f"updating narrative[{i}] ({update_type}) id={narrative.id}")

            # Update Narrative
            # is_default_narrative=True: only add event_id (no other updates)
            # is_main_narrative=True: full update (async LLM summary)
            # is_main_narrative=False: basic update only (associate Event, update dynamic_summary)
            await narrative_service.update_with_event(
                narrative,
                ctx.event,
                is_main_narrative=is_main,
                is_default_narrative=is_default
            )
            ctx.substeps_4.append(f"[4.4.{i+1}] ✓ Narrative: {narrative.narrative_info.name} ({update_type})")
            logger.info(f"Narrative[{i}] ({update_type}) updated with event {ctx.event.id}")

    # =========================================================================
    # 4.5 Update Session continuity anchor (last_response / narrative)
    #
    # The anchor must track the LAST MESSAGE VISIBLE IN THE USER'S CHAT BOX,
    # because that is what the user's next reply (especially a short "好"/"yes")
    # is responding to. That message is either:
    #   - the user's own input on a human-triggered turn (is_user_chat), or
    #   - an agent message the agent DELIVERED to the user this turn — even
    #     from a background trigger (a scheduled job / heartbeat can call
    #     notify_owner; from the user's POV that is the
    #     latest interaction).
    # So we anchor when (is_user_chat OR this turn delivered a user message).
    # Pure machine traffic (a job/bus turn that did NOT message the user) still
    # leaves the anchor untouched, so it can't clobber the real exchange.
    # =========================================================================
    from narranexus.platform.schema.hook_schema import WorkingSource as _WS

    src = getattr(ctx, "working_source", None)
    if src is None:
        is_user_chat = True
    elif isinstance(src, _WS):
        is_user_chat = src.is_from_human()
    else:
        try:
            is_user_chat = _WS(str(src)).is_from_human()
        except ValueError:
            is_user_chat = True
    src_str = src.value if hasattr(src, "value") else (str(src) if src else None)

    delivered_user_message = _turn_delivered_user_message(
        execution_result.agent_loop_response, src_str or "chat"
    )

    if ctx.session and execution_result.final_output and (is_user_chat or delivered_user_message):
        ctx.session.last_response = execution_result.final_output
        if is_user_chat:
            # Human turn: Step 1 already set last_query / current_narrative_id.
            # Note: do not update last_query_time (keep the user's query time).
            ctx.substeps_4.append("[4.5] ✓ Session persisted (including last_response)")
        else:
            # Proactive delivery (background trigger that messaged the user):
            # Step 1 skipped the anchor, so set it here. The agent's message is
            # now the last visible thing; there is no preceding user query, so
            # clear last_query and key continuity off last_response.
            if main_narrative:
                ctx.session.current_narrative_id = main_narrative.id
            ctx.session.last_query = ""
            ctx.session.last_query_time = datetime.now(timezone.utc)
            ctx.substeps_4.append(
                f"[4.5] ✓ Session anchored to proactive delivery "
                f"(source={src_str}, narrative={main_narrative.id if main_narrative else None})"
            )
        logger.debug(f"Updated Session anchor: last_response={execution_result.final_output[:50]}...")
        await session_service.save_session(ctx.session)
        logger.debug(f"session persisted session_id={ctx.session.session_id}")
    elif ctx.session and execution_result.final_output:
        ctx.substeps_4.append(
            f"[4.5] ↪ Session anchor unchanged (no user-visible delivery, source={src_str})"
        )

    # =========================================================================
    # 4.6 Record LLM cost (fire-and-forget, never blocks the pipeline)
    # =========================================================================
    if execution_result.input_tokens > 0 or execution_result.output_tokens > 0:
        try:
            db = await get_db_client()
            await record_cost(
                db=db,
                agent_id=ctx.agent_id,
                event_id=ctx.event.id if ctx.event else None,
                call_type="agent_loop",
                model=execution_result.model or "unknown",
                input_tokens=execution_result.input_tokens,
                output_tokens=execution_result.output_tokens,
                sdk_cost_usd=execution_result.total_cost_usd or None,
                cache_read_tokens=execution_result.cache_read_tokens,
                cache_creation_tokens=execution_result.cache_creation_tokens,
                num_turns=execution_result.num_turns,
            )
            cost_display = (
                f"${execution_result.total_cost_usd:.6f}"
                if execution_result.total_cost_usd
                else f"{execution_result.input_tokens}+{execution_result.output_tokens} tokens"
            )
            ctx.substeps_4.append(f"[4.6] ✓ Cost recorded: {cost_display}")
        except Exception as e:
            logger.warning(f"Cost recording failed (non-blocking): {e}")

    # NOTE (2026-07-29): step 4.7 used to persist the resumable CLI session
    # handle here, and its placement after 4.0/4.5 was load-bearing because the
    # handle was anchored to the post-routing narrative. The claude adapter now
    # authors the CLI transcript itself every turn with a fresh session id, so
    # there is no handle to store and no anchor to keep aligned — which is also
    # why a mid-turn narrative switch can no longer cost a cold start. See
    # adapters/claude/transcript.py.

    # =========================================================================
    # 4.8 Temporal guard — DIAGNOSTIC ONLY (2026-08-18)
    #
    # Reads the replies the owner already received and records a signal when
    # the agent asserted a date or weekday for "today" that contradicts the
    # clock. It does not rewrite the reply, does not re-prompt, does not stop
    # anything: the message has already been delivered by the time we get
    # here, and 铁律 #15/#16 put "the platform corrects the model's output"
    # firmly out of bounds.
    #
    # Its purpose is to answer a question we could not otherwise answer
    # honestly — how often the prompt rules and date tools are actually
    # failing on real traffic. Per the incident lessons (§4 L3, §5), that
    # belongs in a queryable table rather than in log archaeology.
    #
    # Placed last, after every persistence step, so that even a pathological
    # failure inside it cannot cost the turn any real work.
    # =========================================================================
    if delivered_user_message:
        try:
            reply_texts = _owner_visible_reply_texts(
                execution_result.agent_loop_response, src_str or "chat"
            )
            if reply_texts:
                from narranexus.platform.repository import UserRepository
                from narranexus.platform.utils.temporal_guard import (
                    record_date_claim_mismatches,
                )

                db = await get_db_client()
                user_tz = (
                    await UserRepository(db).get_user_timezone(ctx.user_id)
                    if ctx.user_id else DEFAULT_TIMEZONE
                )
                mismatches = await record_date_claim_mismatches(
                    db,
                    "\n".join(reply_texts),
                    user_tz,
                    agent_id=ctx.agent_id,
                    user_id=ctx.user_id or "",
                    event_id=ctx.event.id if ctx.event else "",
                )
                if mismatches:
                    ctx.substeps_4.append(
                        f"[4.8] ⚠ Temporal guard: {len(mismatches)} date claim(s) "
                        f"disagree with the clock (recorded, reply untouched)"
                    )
        except Exception as e:  # noqa: BLE001 — a probe must never cost a turn
            logger.warning(f"Temporal guard failed (non-blocking): {e}")

    # =========================================================================
    # Complete
    # =========================================================================
    yield ProgressMessage(
        step="4",
        title="Persist Results",
        description=f"✓ Round={current_round}, Event={ctx.event.id}, Narratives={len(unique_narratives)}",
        status=ProgressStatus.COMPLETED,
        details={
            "round": current_round,
            "event_id": ctx.event.id,
            "narratives_updated": len(unique_narratives),
            "total_toolcalls": total_toolcalls,
        },
        substeps=ctx.substeps_4
    )

