"""
Event Prompt building implementation

@file_name: prompt_builder.py
@author: NetMind.AI
@date: 2025-12-22
@description: Event Prompt assembly
"""

from __future__ import annotations

from typing import Dict

from ..models import Event
from .prompts import (
    EVENT_HISTORY_HEAD_PROMPT,
    EVENT_HISTORY_TAIL_PROMPT,
    EVENT_DETAIL_PROMPT_TEMPLATE,
)


class EventPromptBuilder:
    """
    Event Prompt Builder

    Responsibilities:
    - Generate head and tail common text for Event Prompts
    - Generate detailed Prompt for a single Event
    """

    @staticmethod
    async def get_head_tail() -> Dict[str, str]:
        """
        Generate head and tail common text for Event Prompts

        Returns:
            {"head": str, "tail": str}
        """
        return {
            "head": EVENT_HISTORY_HEAD_PROMPT,
            "tail": EVENT_HISTORY_TAIL_PROMPT
        }

    @staticmethod
    async def build_single(event: Event, order: str) -> str:
        """
        Generate detailed Prompt for a single Event

        Args:
            event: Event object
            order: Event sequence number

        Returns:
            Event Prompt text
        """
        # Module instance descriptions
        module_instances_prompt = ""
        for module_instance in event.module_instances:
            module_instances_prompt += f"\n\t- Module Class: {module_instance.module_class}"

        event_prompt = EVENT_DETAIL_PROMPT_TEMPLATE.format(
            order=order,
            event_id=event.id,
            narrative_id=event.narrative_id,
            created_at=event.created_at,
            updated_at=event.updated_at,
            trigger=event.trigger,
            trigger_source=event.trigger_source,
            env_context=event.env_context,
            module_instances_prompt=module_instances_prompt,
            event_log=event.event_log,
            final_output=event.final_output,
        )
        return event_prompt
