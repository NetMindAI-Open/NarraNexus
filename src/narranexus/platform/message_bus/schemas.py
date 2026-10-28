"""
@file_name: schemas.py
@author: NarraNexus
@date: 2026-04-02
@description: Pydantic data models for the MessageBus service

Defines the core data structures used across all MessageBus implementations:
- BusMessage: A message sent within a channel
- BusChannel: A communication channel (group or direct)
- BusChannelMember: Channel membership with read/processed cursors
- BusAgentInfo: Agent registration and discovery metadata
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Union

from pydantic import BaseModel


# Timestamps can be str (raw from SQLite before auto-parse) or datetime (after auto-parse)
Timestamp = Union[str, datetime]


def canonical_ts(value) -> str:
    """A cursor-comparable ISO-8601 string.

    Both cursors are TEXT and compared lexicographically, while the sqlite
    backend auto-parses ``*_at`` columns into ``datetime`` on read. A datetime
    stringified the default way becomes ``"YYYY-MM-DD HH:MM:SS"`` — space, no
    'T' — and since 'T' (0x54) sorts above ' ' (0x20) such a cursor sits BELOW
    every real ``created_at``, making every message look unprocessed forever.
    That cost us a re-trigger loop once; it gets exactly one home.

    ``value`` must not be None: ``bus_messages.created_at`` is NOT NULL, and a
    hand-built ``BusMessage`` without one would stringify to ``"None"`` — which
    sorts ABOVE every real timestamp and, written as an ack high-water, would
    silence the lane for good.
    """
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class BusMessage(BaseModel):
    """A message sent within a MessageBus channel."""

    model_config = {"arbitrary_types_allowed": True}

    message_id: str
    channel_id: str
    from_agent: str
    content: str
    msg_type: str = "text"
    mentions: Optional[List[str]] = None  # list of agent_ids or ["@everyone"]
    # Provenance of `mentions`: None when the sender wrote them, or
    # "default_responder" when a team room's route supplied one because the
    # user named nobody. The prompt needs the difference to avoid telling an
    # agent it was singled out when it was merely the fallback.
    routed_by: Optional[str] = None
    # Bus-attachment dicts (file_id/original_name/mime_type/size_bytes/category/
    # rel_path). Populated when the sender attaches files; None for text-only.
    # The trigger renders these into Read-tool markers at delivery time.
    attachments: Optional[List[dict]] = None
    # Monologue/reply boundary, when the producing run had one. None means
    # "no boundary recorded" — a legacy message or a path without monologue —
    # and the renderer treats that as one block.
    segments: Optional[List[dict]] = None
    # events row id of the turn that produced this message. Stamped by the
    # trigger's in-turn room post AND by the agent's own bus sends (identity
    # header, 2026-08-14), so a present id does not mean "the platform posted
    # it". None for user messages, legacy rows, and any send whose caller
    # could not tell which turn it was in.
    event_id: Optional[str] = None
    # WHICH KIND of turn produced this message — "chat"/"job"/… (the sender
    # was running an errand for its owner, so this is a QUESTION) vs
    # "message_bus" (the sender was answering a peer, so this is a REPLY).
    # MessageBusTrigger selects the recipient's directive from it. None on
    # legacy rows and on adapters that cannot forward the injected header.
    sender_turn_source: Optional[str] = None
    # The trigger TREE the sending run belonged to (events.root_run_id). The run
    # this message wakes up inherits it, which is how a cascade stop reaches
    # past an agent→agent hop. None for user messages and legacy rows.
    root_run_id: Optional[str] = None
    # A long message sent in ordered parts: 1-based index and total, and the
    # group id (the first part's message_id) tying the parts together. All
    # None on an ordinary single-row message. The trigger holds an incomplete
    # group back and hands the turn ONE reassembled message (multipart.py).
    part_index: Optional[int] = None
    part_count: Optional[int] = None
    part_group: Optional[str] = None
    # When this message is the REASSEMBLY of several stored rows (a long
    # message sent in ordered parts), the ids of every row it stands for, in
    # order. None for an ordinary single-row message. Not a column: it exists
    # only on the in-memory message the trigger hands to a turn, so that
    # per-row bookkeeping (delivery receipts) can reach every part.
    part_message_ids: Optional[List[str]] = None
    created_at: Any = None


class BusChannel(BaseModel):
    """A communication channel in the MessageBus."""

    model_config = {"arbitrary_types_allowed": True}

    channel_id: str
    name: str
    channel_type: str = "group"
    created_by: str
    created_at: Any = None


class BusChannelMember(BaseModel):
    """Channel membership record with read and processed cursors."""

    model_config = {"arbitrary_types_allowed": True}

    channel_id: str
    agent_id: str
    joined_at: Any = None
    last_read_at: Any = None
    last_processed_at: Any = None


class BusAgentInfo(BaseModel):
    """Agent registration and discovery metadata."""

    model_config = {"arbitrary_types_allowed": True}

    agent_id: str
    owner_user_id: str
    capabilities: List[str] = []
    description: str = ""
    visibility: str = "private"
    registered_at: Any = None
    last_seen_at: Any = None
