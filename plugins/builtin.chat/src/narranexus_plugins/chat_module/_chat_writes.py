"""
@file_name: _chat_writes.py
@author: Bin Liang
@date: 2026-08-20
@description: The single writer for the bootstrap greeting chat row.

The greeting is persisted by TWO callers — ChatModule.persist_turn (the
lazy prepend on a turn whose history is still empty) and the step_1
provision-time seed (driven by bootstrap/greeting_seed) — so the row's shape
and its timestamp constraints live HERE, in the chat module that owns the
`instance_json_format_memory_chat` table, instead of being copied into
bootstrap/ where the two would drift apart.

Load-bearing constraints, in one place:
  - meta_data.bootstrap = True marks the row for the frontend / auto-delete.
  - The timestamp is anchored to turn-START (event.created_at) minus 1ms, NOT
    to `utc_now()` and NOT to the agent's creation time:
      * turn-start - 1ms sorts strictly before the user's first message (stamped
        turn-start), so a timestamp-ascending render (chat-history API +
        frontend timeline) keeps the greeting on top.
      * turn-start ≈ when the user pressed enter, so it satisfies the TIME half
        of the frontend's session-copy dedup — buildTimeline.ts dedups a
        no-event_id session message against history by (role, content) AND a
        5-minute window; anchoring further back (e.g. agent creation) would blow
        the window. NOTE the window is only one half: dedup also needs the
        content to MATCH, and the seeded row's content is the English
        `bootstrap_greeting` metadata while the frontend session copy is the
        localized text — so in a non-English UI the copy is NOT deduped and the
        greeting renders twice. That is a pre-existing limitation (the lazy hook
        wrote the same English content), tracked for a separate fix (give the
        bootstrap row a stable identity the frontend can dedup on instead of
        content); this anchor does not claim to close it.
  - The datetime is normalised to aware-UTC before isoformat(), so the emitted
    string carries a `+00:00`/`Z` offset and the browser's `new Date()` parses
    it as UTC rather than local time (the naive-datetime-on-MySQL trap).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from loguru import logger

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient

# ChatModule.config.name; EventMemoryRepository maps it →
# instance_json_format_memory_chat (see _get_instance_json_format_table_name).
_CHAT_MODULE_NAME = "ChatModule"


def build_bootstrap_greeting_row(
    greeting: str,
    turn_started_at: datetime,
    instance_id: str,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The one definition of a bootstrap greeting message row.

    `turn_started_at` is the turn's Event.created_at (aware). A naive value is
    assumed to be UTC so the serialized timestamp is never tz-ambiguous.
    """
    if turn_started_at.tzinfo is None:
        turn_started_at = turn_started_at.replace(tzinfo=timezone.utc)
    ts_iso = (turn_started_at - timedelta(milliseconds=1)).isoformat()
    meta: Dict[str, Any] = {
        "timestamp": ts_iso,
        "instance_id": instance_id,
        "bootstrap": True,
    }
    if event_id is not None:
        meta["event_id"] = event_id
    return {"role": "assistant", "content": greeting, "meta_data": meta}


async def agent_chat_has_history(
    db: "AsyncDatabaseClient", agent_id: str, user_id: str
) -> bool:
    """True if ANY ChatModule instance of (agent, user) already holds messages.

    The greeting's idempotency scope (Shenzhen-r2 B2): per-INSTANCE emptiness
    is the wrong unit — every new narrative creates a fresh empty chat
    instance, so an instance-scoped guard re-greets on each one while
    bootstrap is active, and the extra assistant row pops in next to whatever
    the user just asked ("one question, two replies"). First contact is a
    per-(agent, user) fact: any prior chat history anywhere means the agent
    has already been greeted. Status is deliberately ignored — history in an
    archived/cancelled instance still proves prior contact.
    """
    from narranexus.platform.repository.event_memory_repository import (
        EventMemoryRepository,
    )

    rows = await db.get(
        "module_instances",
        {"agent_id": agent_id, "user_id": user_id, "module_class": _CHAT_MODULE_NAME},
    )
    repo = EventMemoryRepository(agent_id, user_id, db)
    for row in rows or []:
        instance_id = row.get("instance_id")
        if not instance_id:
            continue
        mem = await repo.search_instance_json_format_memory(
            _CHAT_MODULE_NAME, instance_id
        )
        if mem and mem.get("messages"):
            return True
    return False


async def seed_bootstrap_greeting(
    db: "AsyncDatabaseClient",
    agent_id: str,
    user_id: str,
    instance_id: str,
    greeting: str,
    turn_started_at: datetime,
) -> bool:
    """Persist `greeting` as `instance_id`'s FIRST message, idempotently.

    No-op (returns False) if the instance already holds ANY message — the same
    `len(messages) == 0` guard ChatModule.persist_turn uses. That makes
    this safe to call every turn for the primary instance: a fresh instance is
    seeded once; on later turns (or after the hook already wrote the greeting)
    the history is non-empty and this skips, so the greeting is never doubled
    and an existing conversation is never reordered. Best-effort — any failure
    logs and returns False, leaving the hook prepend as the fallback.
    """
    from narranexus.platform.repository.event_memory_repository import (
        EventMemoryRepository,
    )

    try:
        repo = EventMemoryRepository(agent_id, user_id, db)
        existing = await repo.search_instance_json_format_memory(
            _CHAT_MODULE_NAME, instance_id
        )
        if existing and existing.get("messages"):
            return False  # already has history — hook / prior seed handled it
        # Cross-instance guard (Shenzhen-r2 B2): a fresh instance born for a
        # NEW narrative must not re-greet an agent that already has history
        # elsewhere. Kept AFTER the own-instance check — that one is cheaper
        # and does not depend on module_instances registration.
        if await agent_chat_has_history(db, agent_id, user_id):
            return False
        row = build_bootstrap_greeting_row(greeting, turn_started_at, instance_id)
        ok = await repo.add_instance_json_format_memory(
            _CHAT_MODULE_NAME,
            instance_id,
            {"messages": [row], "updated_at": row["meta_data"]["timestamp"]},
        )
        if ok:
            logger.info(
                f"[bootstrap] seeded greeting into chat instance "
                f"{instance_id} (agent={agent_id})"
            )
        return bool(ok)
    except Exception as e:  # noqa: BLE001 — best-effort; hook prepend is the fallback
        logger.warning(
            f"[bootstrap] greeting seed write failed for {agent_id}/{instance_id}: {e}"
        )
        return False
