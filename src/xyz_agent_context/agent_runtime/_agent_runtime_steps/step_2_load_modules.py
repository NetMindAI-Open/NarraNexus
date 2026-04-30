"""
@file_name: step_2_load_modules.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 2 - Load Modules and decide execution path

Select and load relevant modules based on input content, while deciding the execution path (AGENT_LOOP or DIRECT_TRIGGER).
"""

from __future__ import annotations

from typing import AsyncGenerator, Union, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ProgressMessage, ProgressStatus, ErrorMessage
from xyz_agent_context.module.memory_module import get_memory_module
from .step_display import (
    format_instances_for_display,
    format_execution_type_for_display,
)

if TYPE_CHECKING:
    from .context import RunContext


@timed("step.2_load_modules")

async def step_2_load_modules(
    ctx: "RunContext"
) -> AsyncGenerator[Union[ProgressMessage, ErrorMessage], None]:
    """
    Step 2: Load Modules and decide execution path

    Use ModuleService to load modules and decide the execution path.

    Args:
        ctx: Run context

    Yields:
        ProgressMessage: Progress messages
    """
    # Send Running status
    yield ProgressMessage(
        step="2",
        title="🧩 Module Loading",
        description="Loading instances and determining execution path...",
        status=ProgressStatus.RUNNING,
        substeps=ctx.substeps_2
    )

    # Use ModuleService to load modules and decide execution path
    # Default to Instance intelligent decision mode (LLM-driven)
    # Get working_source string value
    working_source_str = None
    if ctx.working_source:
        working_source_str = (
            ctx.working_source.value
            if hasattr(ctx.working_source, 'value')
            else str(ctx.working_source)
        )

    load_result = await ctx.module_service.load_modules(
        narrative_list=ctx.narrative_list,
        input_content=ctx.input_content,
        use_instance_decision=True,
        markdown_history=ctx.markdown_history,
        awareness=ctx.awareness,
        working_source=working_source_str,
    )
    ctx.load_result = load_result

    # Surface LLM decision error to frontend (fallback was used, execution continues)
    if load_result.llm_error:
        yield ErrorMessage(
            error_message=load_result.llm_error,
            error_type="module_decision_error",
        )

    # Extract data from ModuleLoadResult
    active_instances = load_result.active_instances
    execution_type = load_result.execution_type

    # Extract module object list (used for hook execution, etc.)
    ctx.module_list = [inst.module for inst in active_instances if inst.module is not None]

    # Add Agent-level modules (not managed through Instance mechanism)
    # MemoryModule: responsible for EverMemOS writing and other memory management tasks
    memory_module = get_memory_module(ctx.agent_id, ctx.user_id)
    ctx.module_list.append(memory_module)
    logger.debug("Added MemoryModule to module_list")

    logger.info(
        f"Instances loaded: count={len(active_instances)}, "
        f"execution_type={execution_type.value}"
    )

    # Format for user-friendly display
    display_data = format_instances_for_display(active_instances)
    exec_display = format_execution_type_for_display(execution_type.value)

    # Generate substeps (user-friendly)
    substeps = []
    for item in display_data["items"]:
        substeps.append(f"{item['icon']} {item['module']} - {item['desc']}")
    substeps.append(f"{exec_display['icon']} {exec_display['text']}")

    # Developer logs
    instance_details = []
    for i, inst in enumerate(active_instances):
        status_value = (
            inst.status.value if hasattr(inst.status, 'value') else inst.status
        )
        logger.info(
            f"  🧩 Instance: {inst.instance_id} ({inst.module_class}) - {status_value}"
        )
        ctx.substeps_2.append(f"[2.{i+1}] ✓ {inst.instance_id}")
        instance_details.append({
            "instance_id": inst.instance_id,
            "module_class": inst.module_class,
            "status": status_value
        })

    # Send Completed status
    yield ProgressMessage(
        step="2",
        title="🧩 Module Loading",
        description=f"{display_data['summary']} → {execution_type.value}",
        status=ProgressStatus.COMPLETED,
        details={
            "display": display_data,
            "execution": exec_display,
            "instances": instance_details,
            "execution_type": execution_type.value,
            # LLM decision info
            "decision_reasoning": load_result.decision_reasoning,
            "changes_summary": load_result.changes_summary,
            "changes_explanation": load_result.changes_explanation,
            "relationship_graph": load_result.relationship_graph,
        },
        substeps=substeps
    )
