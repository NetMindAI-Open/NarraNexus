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
    # 4.1 Record Trajectory
    # =========================================================================
    logger.info("📊 Step 4.1: Recording Trajectory")

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
    logger.success(f"✅ Trajectory recorded: Round {current_round}")

    # =========================================================================
    # 4.2 Update Markdown statistics
    # =========================================================================
    logger.info("📊 Step 4.2: Updating Markdown Statistics")

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
    logger.success("✅ Markdown statistics updated")

    # =========================================================================
    # 4.3 Update Event
    # =========================================================================
    logger.info("💾 Step 4.3: Updating Event in database")

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
    logger.success(f"✅ Event updated: event_id={ctx.event.id}")

    # =========================================================================
    # 4.4 Update Narratives
    # =========================================================================
    logger.info("💾 Step 4.4: Updating Narratives with Event")

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
        
        logger.info(f"    Updating Narrative[{i}] ({update_type}): id={narrative.id}")

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
        logger.success(f"    ✅ Narrative[{i}] ({update_type}) updated with event {current_event.id}")

    # =========================================================================
    # 4.5 Update Session (add last_response and persist)
    # =========================================================================
    if ctx.session and execution_result.final_output:
        ctx.session.last_response = execution_result.final_output
        # Note: do not update last_query_time, it should remain as the user's query time (already updated in Step 1)
        logger.debug(f"Updated Session.last_response: {execution_result.final_output[:50]}...")
        
        # Persist Session (including last_response)
        await session_service.save_session(ctx.session)
        ctx.substeps_4.append("[4.5] ✓ Session persisted (including last_response)")
        logger.success(f"✅ Session persisted: {ctx.session.session_id}")

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

    logger.info("="*80)
    logger.success("🎉 AgentRuntime.run() completed successfully")
    logger.info("="*80 + "\n")
