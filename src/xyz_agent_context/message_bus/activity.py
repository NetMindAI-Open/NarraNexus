"""
@file_name: activity.py
@author: NarraNexus
@date: 2026-07-22
@description: Public read surface for team-room live-activity status.

Re-export facade over ``_bus_activity`` for consumers outside the
``message_bus`` package (the team-chat GET route). Only the READ side is
public — the write side (mark_running / update_phase / mark_idle) belongs to
``MessageBusTrigger`` inside this package and stays private.
"""

from xyz_agent_context.message_bus._bus_activity import (
    ACTIVITY_STALE_SECONDS,
    get_channel_activity,
    is_live,
)

__all__ = [
    "ACTIVITY_STALE_SECONDS",
    "get_channel_activity",
    "is_live",
]
