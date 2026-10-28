"""
@file_name: step_1_5_init_markdown.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 1.5 - Initialize/read Markdown history

Initialize the Markdown file and read historical content for Instance decision-making.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from loguru import logger
from narranexus.platform.utils.logging import timed

if TYPE_CHECKING:
    from .context import RunContext
    from narranexus.platform.narrative import NarrativeMarkdownManager


@timed("step.1_5_init_markdown")

async def step_1_5_init_markdown(
    ctx: "RunContext",
    markdown_manager: "NarrativeMarkdownManager",
    read_history: bool = True,
) -> None:
    """
    Step 1.5: Initialize/read Markdown history

    If the main Narrative exists, initialize the Markdown and read historical content.
    Also saves pre-decision instances for trajectory comparison.

    Args:
        ctx: Run context
        markdown_manager: Markdown manager
        read_history: False on fast turns — reading the markdown only
            feeds the instance-decision LLM, which fast mode bypasses.
            The other two outputs are load-bearing regardless: the
            previous_instances snapshot feeds step_4's trajectory record,
            and initialize_markdown keeps first-hit narratives' statistics
            alive — so they always run.

    Note: This step does not produce ProgressMessage (silent execution)
    """
    main_narrative = ctx.main_narrative

    if main_narrative:
        # Save pre-decision instances (deep copy to avoid reference issues)
        ctx.previous_instances = copy.deepcopy(main_narrative.active_instances)

        # Initialize markdown (if it does not exist)
        await markdown_manager.initialize_markdown(main_narrative)

        if not read_history:
            ctx.substeps_1_5.append(
                "[1.5.1] ✓ Markdown initialized (history read skipped: fast turn)"
            )
            return

        # Read markdown history (for Instance decision-making)
        ctx.markdown_history = await markdown_manager.read_markdown(main_narrative.id)

        logger.info(f"Markdown history loaded: {len(ctx.markdown_history)} chars")
        ctx.substeps_1_5.append(
            f"[1.5.1] ✓ Markdown history loaded: {ctx.markdown_history}"
        )
    else:
        ctx.substeps_1_5.append(
            "[1.5.1] ✖ No main narrative found, we just created a new one"
        )
