"""
@file_name: agent_profile_service.py
@author: NarraNexus
@date: 2026-08-18
@description: Public seam for changing an agent's display name / description.

One function, because a rename is a transaction rather than a column write —
see ``_agent_profile_impl.profile_write`` for the incident that made that
distinction load-bearing. Routes, MCP tools and provisioning flows all come
through here; nothing outside this package writes ``agents.agent_name``.
"""
from ._agent_profile_impl.profile_write import (
    AgentProfileWrite,
    apply_agent_profile_change,
)

__all__ = ["AgentProfileWrite", "apply_agent_profile_change"]
