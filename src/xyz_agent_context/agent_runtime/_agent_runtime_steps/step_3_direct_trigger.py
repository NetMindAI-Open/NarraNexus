"""
@file_name: step_3_direct_trigger.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 3 - Direct Trigger (CASE2: DIRECT_TRIGGER)

Directly call Module's Trigger (skip Agent Loop).
This is the processing path for simple tasks with clear intent, no LLM reasoning needed.
"""

from __future__ import annotations

from typing import List, Dict, Any, TYPE_CHECKING

from xyz_agent_context.schema import PathExecutionResult
from xyz_agent_context.utils.logging import timed
from xyz_agent_context.utils.mcp_executor import mcp_tool_executor

if TYPE_CHECKING:
    pass


@timed("step.3_direct_trigger")
async def step_3_direct_trigger(
    active_instances: List[Any],
    module_class: str,
    trigger_name: str,
    args: Dict[str, Any]
) -> PathExecutionResult:
    """
    Step 3: Direct Trigger (CASE2: DIRECT_TRIGGER)

    Directly call the specified Module's MCP Tool.

    Args:
        active_instances: ModuleInstance list (each instance is bound to a module object)
        module_class: Module class name (e.g., "ChatModule")
        trigger_name: Trigger/Tool name
        args: Tool arguments

    Returns:
        PathExecutionResult: Unified execution result
    """
    mcp_server_url = None
    target_module = None

    # Find the corresponding Module instance by module_class
    for instance in active_instances:
        # instance.module is the actual Module object bound at runtime
        module = instance.module
        if module is None:
            continue
        # Match module_class (class name)
        if instance.module_class == module_class:
            mcp_config = await module.get_mcp_config()
            if mcp_config:
                mcp_server_url = mcp_config.server_url
                target_module = module
            break

    if not mcp_server_url:
        return PathExecutionResult(
            final_output=f"Cannot find MCP configuration for Module {module_class}",
            execution_steps=[],
            response_count=1,
            agent_loop_response=[],
            ctx_data=None
        )

    result = await mcp_tool_executor(mcp_server_url, trigger_name, args)

    return PathExecutionResult(
        final_output=(
            f"Executed {module_class}'s {trigger_name}, "
            f"args: {args}, result: {result}"
        ),
        execution_steps=[{
            "type": "direct_trigger",
            "module_class": module_class,
            "trigger_name": trigger_name,
            "args": args,
            "result": result
        }],
        response_count=1,
        agent_loop_response=[result],
        ctx_data=None
    )
