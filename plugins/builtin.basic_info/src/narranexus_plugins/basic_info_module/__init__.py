"""BasicInfoModule package.

Public surface: the dialect-safe narrative/event read helpers that the
AgentDataStore seam's DirectStore and the backend narrative routes both call
(so callers import the PACKAGE, not the private ``_narrative_reads`` leaf).
"""
from narranexus_plugins.basic_info_module._narrative_reads import (
    fetch_narrative_view,
    fetch_event_view,
    check_narrative_switch,
    narrative_chat_history,
)

__all__ = [
    "fetch_narrative_view",
    "fetch_event_view",
    "check_narrative_switch",
    "narrative_chat_history",
]
