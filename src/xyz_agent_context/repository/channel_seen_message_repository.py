"""
@file_name: channel_seen_message_repository.py
@date: 2026-05-08
@description: Generic durable dedup store for ALL IM channel triggers.

Generalisation of `LarkSeenMessageRepository`: a multi-channel version
keyed on (channel, dedup_key) so Lark, Slack, Telegram, ... can dedup
independently against the same `channel_seen_messages` table.

**The caller owns the dedup key namespace.** The stored value is whatever
string the caller passes — NOT necessarily a bare platform message id.
``ChannelDedupStore`` passes ``f"{agent_id}:{message_id}"`` for multi-agent
channels (Matrix fans the same room event out to every member agent's
client, so a bare id would let whichever agent's sync landed first drop
every other agent's copy — the 2026-07-16 group-room silent-loss bug).
Single-tenant callers (Lark) pass a bare id. Any NEW Layer-3 caller MUST
namespace its key by agent when more than one agent can receive the same
platform id, or it re-introduces that bug. The physical column is still
named ``message_id`` (schema unchanged, 铁律 #6) but semantically holds
this composite dedup key.

Contract identical to the Lark version:
- ``mark_seen(dedup_key)`` — atomic INSERT-or-detect-UNIQUE. Returns
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
    """Persistent dedup store keyed on (channel, dedup_key).

    ``dedup_key`` is caller-composed — see the module docstring. For
    multi-agent channels it must be namespaced by agent_id.
    """

    TABLE = "channel_seen_messages"

    def __init__(self, channel: str, db_client):
        if not channel:
            raise ValueError("channel must be a non-empty string")
        self._channel = channel
        self._db = db_client

    @property
    def channel(self) -> str:
        return self._channel

    async def mark_seen(self, dedup_key: str) -> bool:
        """
        Record (channel, dedup_key) as seen. Atomic.

        ``dedup_key`` is caller-composed. For multi-agent channels it must
        already be namespaced by agent (e.g. ``f"{agent_id}:{message_id}"``)
        — passing a bare platform id re-introduces the group-room
        silent-loss bug (see module docstring). Stored in the ``message_id``
        column (physical name unchanged for schema stability).

        Returns:
            True  — newly inserted → caller should process the message.
            False — already present → caller must drop the message.

        Raises:
            Any non-UNIQUE backend exception (caller fails open).
        """
        if not dedup_key:
            # Empty key → caller decides. Matches Lark behaviour.
            return True

        now = datetime.now(timezone.utc)
        try:
            await self._db.insert(
                self.TABLE,
                {
                    "channel": self._channel,
                    "message_id": dedup_key,
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
                f"ChannelSeenMessageRepository.mark_seen({self._channel}, {dedup_key}): "
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
