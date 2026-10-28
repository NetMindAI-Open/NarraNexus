"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-12-24
@description: AgentRuntime Steps module exports

This module contains the implementation of each step in the AgentRuntime.run() method.
Each step is extracted as an independent function, keeping the main function clean.

Module structure:
    _agent_runtime_steps/
    ├── __init__.py              # This file - unified exports
    ├── context.py               # RunContext context class
    ├── step_0_initialize.py     # Step 0: Initialization
    ├── step_1_select_narrative.py
    ├── step_1_5_init_markdown.py
    ├── step_2_load_modules.py
    ├── step_2_5_sync_instances.py  # Step 2.5: Sync Instances
    ├── step_3_execute_path.py   # Execution path selector
    ├── step_3_agent_loop.py     # AGENT_LOOP execution path
    ├── step_3_direct_trigger.py # DIRECT_TRIGGER execution path
    ├── step_4_persist_results.py # Step 4: Persistence
    └── step_5_execute_hooks.py

Note: Step 6 (Process Hook Callbacks) has been moved to HookManager.process_hook_callbacks()
"""

# Context class
from .context import RunContext

# Step user-friendly display
from .step_display import (
    format_narrative_for_display,
    format_instances_for_display,
    format_execution_type_for_display,
    format_tool_call_for_display,
    format_thinking_for_display,
    module_display,
    TOOL_DISPLAY_CONFIG,
)

# Step 0 - Initialization
from .step_0_initialize import step_0_initialize

# Step 1 series - Narrative selection
from .step_1_select_narrative import step_1_select_narrative
from .step_1_fast_select import step_1_fast_select
from .step_1_5_init_markdown import step_1_5_init_markdown

# Step 2 series - Module loading
from .step_2_load_modules import step_2_load_modules
from .step_2_5_sync_instances import step_2_5_sync_instances

# Step 3 series - Execution path
from .step_3_execute_path import step_3_execute_path
from .step_3_agent_loop import step_3_agent_loop
from .step_3_direct_trigger import step_3_direct_trigger

# Step 4 - Persistence
from .step_4_persist_results import step_4_persist_results

# Step 5 - Hook execution
from .step_5_execute_hooks import step_5_execute_hooks
# Note: Step 6 (Process Hook Callbacks) has been moved to HookManager.process_hook_callbacks()


__all__ = [
    # Context
    "RunContext",

    # Step user-friendly display
    "format_narrative_for_display",
    "format_instances_for_display",
    "format_execution_type_for_display",
    "format_tool_call_for_display",
    "format_thinking_for_display",
    "module_display",
    "TOOL_DISPLAY_CONFIG",

    # Step 0 - Initialization
    "step_0_initialize",

    # Step 1
    "step_1_select_narrative",
    "step_1_fast_select",
    "step_1_5_init_markdown",

    # Step 2
    "step_2_load_modules",
    "step_2_5_sync_instances",

    # Step 3
    "step_3_execute_path",
    "step_3_agent_loop",
    "step_3_direct_trigger",

    # Step 4 - Persistence
    "step_4_persist_results",

    # Step 5
    "step_5_execute_hooks",
]
