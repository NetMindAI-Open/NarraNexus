"""
@file_name: lark_seen_message_repository.py
@author: Bin Liang
@date: 2026-04-20
@description: Durable dedup store for incoming Lark WebSocket events (Bug 27).

The trigger's in-memory set is necessary but not sufficient: on every
process restart we'd lose it and Lark's at-least-once delivery would
re-deliver un-acked events, causing the agent to reply to the same user
message twice — sometimes an hour apart, exactly as an operator reported.

This repository exposes one method per operation the trigger needs:

  - ``mark_seen(message_id)`` — atomic "have we processed this message"
    gate. Returns ``True`` the first time (proceed to process),
    ``False`` every subsequent time (drop as duplicate). Survives
    restarts because the state lives in a DB row.

  - ``cleanup_older_than_days(n)`` — bounded retention. Called once at
    trigger startup so the table doesn't grow without bound.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from narranexus.platform.utils.db.dialect_errors import is_unique_violation


class LarkSeenMessageRepository:
    """Persistent dedup store for Lark event ``message_id``.

    Deliberately not a ``BaseRepository[...]`` subclass — the entity is
    trivial (id + timestamp) and the hot path only needs two bespoke
    atomic operations, not CRUD.
    """

    TABLE = "lark_seen_messages"

    def __init__(self, db_client):
        self._db = db_client

    async def mark_seen(self, message_id: str) -> bool:
        """
        Record this message_id as seen. Atomic.

        Returns:
            True  — newly inserted → caller should process the message.
            False — already present → caller must drop the message.

        Implementation: try INSERT first; on unique-constraint violation
        (``UNIQUE(message_id)``) treat as "already seen". This is one
        round-trip per event and safe under concurrent SDK workers
        because the DB enforces the constraint atomically.
        """
        if not message_id:
            # Empty id → we don't know; let the caller decide to process.
            return True

        now = datetime.now(timezone.utc)
        try:
            await self._db.insert(
                self.TABLE,
                {"message_id": message_id, "seen_at": now.isoformat(sep=" ")},
            )
            return True
        except Exception as e:
            # Distinguish UNIQUE-constraint violations (genuine duplicate) from
            # everything else (transient DB trouble). The dual-dialect text match
            # lives in ``is_unique_violation`` so neither driver is imported here.
            if is_unique_violation(e):
                return False
            # Non-UNIQUE failures (connection lost, disk full, etc.) MUST
            # propagate. The trigger's `_should_process_event` catches this
            # and chooses fail-open (process once more, log loudly), which
            # matches the documented intent: silent loss is worse than a
            # rare double-reply. Previously this branch returned False,
            # which fail-closed the message — the OPPOSITE of intent.
            # See H-3 in the 2026-04-21 audit.
            logger.warning(
                f"LarkSeenMessageRepository.mark_seen({message_id}): "
                f"propagating {type(e).__name__}: {e} so caller can fail-open"
            )
            raise

    async def cleanup_older_than_days(self, days: int) -> int:
        """
        Delete rows whose ``seen_at`` is older than ``days`` days ago.

        Called once at trigger startup. Bounds table growth even though
        each row is tiny; 7 days is far longer than any observed Lark
        re-delivery window.

        Returns:
            Number of rows deleted (best-effort; 0 on driver error).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.isoformat(sep=" ")
        try:
            # ``fetch=False`` routes to ``execute_write`` whose contract is
            # to return the rowcount (int) for DML. With ``fetch=True``
            # (default) the call would be typed as SELECT and return [].
            result = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE seen_at < %s",
                (cutoff_str,),
                fetch=False,
            )
            return int(result) if isinstance(result, (int, float)) else 0
        except Exception as e:
            logger.warning(
                f"LarkSeenMessageRepository.cleanup_older_than_days({days}): "
                f"{type(e).__name__}: {e}"
            )
            return 0
