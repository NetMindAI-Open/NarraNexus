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
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ProgressMessage, ProgressStatus
from xyz_agent_context.narrative import EventLogEntry
from xyz_agent_context.agent_runtime.execution_state import ExecutionState
from xyz_agent_context.utils.cost_tracker import record_cost
from xyz_agent_context.utils.db_factory import get_db_client

if TYPE_CHECKING:
    from .context import RunContext
    from xyz_agent_context.narrative import (
        EventService,
        NarrativeService,
        NarrativeMarkdownManager,
        TrajectoryRecorder,
        SessionService,
    )


def _turn_delivered_user_message(agent_loop_response, working_source: str) -> bool:
    """Did this turn deliver a user-visible message?

    True iff the agent fired a reply tool that surfaces in the user's chat
    (``send_message_to_user_directly`` for any source; plus the per-channel
    reply tools like ``lark_cli`` for IM sources). Uses the same
    ``MessageSourceRegistry`` source-of-truth the ChatModule uses to split
    user-visible replies, so the two never disagree.

    Imports the channel registry (not any concrete Module) on purpose —
    Modules stay hot-pluggable (铁律 #3); the registry is shared infra.
    On any shape/registry mismatch we return False, which only means the
    background-delivery anchor is skipped — the human-turn anchor path is
    unaffected, so we never regress existing behavior.
    """
    try:
        from xyz_agent_context.schema import ProgressMessage
        from xyz_agent_context.channel.message_source_handler import (
            MessageSourceRegistry,
        )

        handler = MessageSourceRegistry.get(working_source)
        for resp in agent_loop_response or []:
            if not (isinstance(resp, ProgressMessage) and resp.details):
                continue
            tool_name = resp.details.get("tool_name", "")
            arguments = resp.details.get("arguments", {})
            if handler.extract_reply_text(tool_name, arguments):
                return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_turn_delivered_user_message: detection failed ({e}); treating as not delivered")
        return False
    return False


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
        from xyz_agent_context.schema import ProgressMessage
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

    if not main_narrative or not execution_result:
        yield ProgressMessage(
            step="4",
            title="Persist Results",
            description="✗ No execution results to save",
            status=ProgressStatus.COMPLETED,
            substeps=ctx.substeps_4
        )
        return

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

    # [IMPORTANT] Sync final_output to the in-memory Event object
    # so that subsequent EverMemOS writes can access the agent's response
    ctx.event.final_output = execution_result.final_output

    ctx.substeps_4.append(f"[4.3] ✓ Event updated: {ctx.event.id}")
    logger.info(f"Event updated: event_id={ctx.event.id}")

    # =========================================================================
    # 4.4 Update Narratives
    # =========================================================================
    for i, narrative in enumerate(ctx.narrative_list):
        # Determine Narrative type
        is_default = narrative.is_special == "default"
        is_main = (i == 0) and not is_default  # Default Narrative is not treated as main Narrative
        
        if is_default:
            update_type = "default"
        elif is_main:
            update_type = "main"
        else:
            update_type = "auxiliary"
        
        logger.debug(f"updating narrative[{i}] ({update_type}) id={narrative.id}")

        if i == 0:
            # First Narrative: use the original Event
            current_event = ctx.event
            await event_service.update_event_narrative_id(ctx.event.id, narrative.id)
        else:
            # Subsequent Narratives: duplicate Event
            current_event = await event_service.duplicate_event_for_narrative(
                ctx.event, narrative.id
            )

        # Update Narrative
        # is_default_narrative=True: only add event_id (no other updates)
        # is_main_narrative=True: full update (LLM + Embedding)
        # is_main_narrative=False: basic update only (associate Event, update dynamic_summary)
        await narrative_service.update_with_event(
            narrative, 
            current_event, 
            is_main_narrative=is_main,
            is_default_narrative=is_default
        )
        ctx.substeps_4.append(f"[4.4.{i+1}] ✓ Narrative: {narrative.narrative_info.name} ({update_type})")
        logger.info(f"Narrative[{i}] ({update_type}) updated with event {current_event.id}")

    # =========================================================================
    # 4.5 Update Session continuity anchor (last_response / narrative)
    #
    # The anchor must track the LAST MESSAGE VISIBLE IN THE USER'S CHAT BOX,
    # because that is what the user's next reply (especially a short "好"/"yes")
    # is responding to. That message is either:
    #   - the user's own input on a human-triggered turn (is_user_chat), or
    #   - an agent message the agent DELIVERED to the user this turn — even
    #     from a background trigger (a scheduled job / heartbeat can call
    #     send_message_to_user_directly; from the user's POV that is the
    #     latest interaction).
    # So we anchor when (is_user_chat OR this turn delivered a user message).
    # Pure machine traffic (a job/bus turn that did NOT message the user) still
    # leaves the anchor untouched, so it can't clobber the real exchange.
    # =========================================================================
    from xyz_agent_context.schema.hook_schema import WorkingSource as _WS

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
            ctx.session.last_query_embedding = None
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
            )
            cost_display = (
                f"${execution_result.total_cost_usd:.6f}"
                if execution_result.total_cost_usd
                else f"{execution_result.input_tokens}+{execution_result.output_tokens} tokens"
            )
            ctx.substeps_4.append(f"[4.6] ✓ Cost recorded: {cost_display}")
        except Exception as e:
            logger.warning(f"Cost recording failed (non-blocking): {e}")

    # =========================================================================
    # Complete
    # =========================================================================
    yield ProgressMessage(
        step="4",
        title="Persist Results",
        description=f"✓ Round={current_round}, Event={ctx.event.id}, Narratives={len(ctx.narrative_list)}",
        status=ProgressStatus.COMPLETED,
        details={
            "round": current_round,
            "event_id": ctx.event.id,
            "narratives_updated": len(ctx.narrative_list),
            "total_toolcalls": total_toolcalls,
        },
        substeps=ctx.substeps_4
    )

