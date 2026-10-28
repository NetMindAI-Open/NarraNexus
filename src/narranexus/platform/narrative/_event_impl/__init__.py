"""
Private implementation of the Event module

This directory contains the concrete implementation of EventService and should not be imported directly externally.

Module list:
- crud: Event creation, read, update
- processor: Event processing and context selection
- prompt_builder: Event Prompt assembly
"""

from .crud import EventCRUD
from .processor import EventProcessor
from .prompt_builder import EventPromptBuilder

__all__ = [
    "EventCRUD",
    "EventProcessor",
    "EventPromptBuilder",
]
