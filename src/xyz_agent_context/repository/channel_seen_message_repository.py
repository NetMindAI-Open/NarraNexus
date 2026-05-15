"""
@file_name: channel_seen_message_repository.py
@date: 2026-05-08
@description: Generic durable dedup store for ALL IM channel triggers.

Generalisation of `LarkSeenMessageRepository`: a multi-channel version
keyed on (channel, message_id) so Lark, Slack, Telegram, ... can dedup
independently against the same `channel_seen_messages` table.

Contract identical to the Lark version:
- ``mark_seen(message_id)`` — atomic INSERT-or-detect-UNIQUE. Returns
  ``True`` the first time (caller processes), ``False`` every subsequent
  call (caller drops as duplicate). Survives restarts.
- ``cleanup_older_than_days(n)`` — bounded retention, called from the
  trigger's daily cleanup tick.

The class is deliberately not a ``BaseRepository[T]`` subclass — the row
is two columns + a timestamp, the hot path needs only two atomic ops, and
``BaseRepository`` would force inventing a CRUD shape that nothing uses.

Failure semantics (preserved from Lark version):
- UNIQUE-constraint violation → ``False`` (genuine duplicate)
- Any other backend error → propagate, caller chooses fail-open. The
  trigger's documented intent is "silent loss is worse than rare
  double-reply", so DB hiccups must not silently drop messages.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger


class ChannelSeenMessageRepository:
    """Persistent dedup store keyed on (channel, message_id)."""

    TABLE = "channel_seen_messages"

    def __init__(self, channel: str, db_client):
        if not channel:
            raise ValueError("channel must be a non-empty string")
        self._channel = channel
        self._db = db_client

    @property
    def channel(self) -> str:
        return self._channel

    async def mark_seen(self, message_id: str) -> bool:
        """
        Record (channel, message_id) as seen. Atomic.

        Returns:
            True  — newly inserted → caller should process the message.
            False — already present → caller must drop the message.

        Raises:
            Any non-UNIQUE backend exception (caller fails open).
        """
        if not message_id:
            # Empty id → caller decides. Matches Lark behaviour.
            return True

        now = datetime.now(timezone.utc)
        try:
            await self._db.insert(
                self.TABLE,
                {
                    "channel": self._channel,
                    "message_id": message_id,
                    "seen_at": now.isoformat(sep=" "),
                },
            )
            return True
        except Exception as e:
            msg = str(e)
            if (
                "UNIQUE constraint failed" in msg     # sqlite
                or "Duplicate entry" in msg            # mysql
                or "1062" in msg                       # mysql err code
            ):
                return False
            # Non-UNIQUE failures (connection lost, disk full, etc.) MUST
            # propagate. The trigger's hot path catches this and chooses
            # fail-open (process once more, log loudly), which matches
            # documented intent.
            logger.warning(
                f"ChannelSeenMessageRepository.mark_seen({self._channel}, {message_id}): "
                f"propagating {type(e).__name__}: {e} so caller can fail-open"
            )
            raise

    async def cleanup_older_than_days(self, days: int) -> int:
        """
        Delete rows for THIS channel whose ``seen_at`` is older than ``days``.

        Cross-channel cleanup is kept independent so a Slack outage cannot
        drag down Lark's retention window.

        Returns:
            Number of rows deleted (best-effort; 0 on driver error).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.isoformat(sep=" ")
        try:
            result = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE channel = %s AND seen_at < %s",
                (self._channel, cutoff_str),
                fetch=False,
            )
            return int(result) if isinstance(result, (int, float)) else 0
        except Exception as e:
            logger.warning(
                f"ChannelSeenMessageRepository.cleanup_older_than_days"
                f"({self._channel}, {days}): {type(e).__name__}: {e}"
            )
            return 0
