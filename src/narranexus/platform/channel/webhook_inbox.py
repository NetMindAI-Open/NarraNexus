"""
@file_name: webhook_inbox.py
@author: Bin Liang
@date: 2026-09-04
@description: The webhook transport's DB-backed inbox (``channel_webhook_events``): the API process pushes a channel's inbound webhook events, the channel trigger pulls them.

Why a table and not an in-process queue: the webhook arrives in the backend
API process and the channel trigger runs in the workers/channels process,
so the hand-off has to cross processes. One host today; this is the seam a
broker replaces later (rule #20). Rows are claimed in id order and kept
(``claimed_at`` set) for a short audit window, then purged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from narranexus.platform.utils import utc_now

TABLE = "channel_webhook_events"


@dataclass(frozen=True)
class WebhookEvent:
    id: int
    channel: str
    agent_id: str
    payload: dict[str, Any]
    received_at: Any


class WebhookInbox:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def push(self, channel: str, agent_id: str, payload: dict[str, Any]) -> None:
        await self._db.insert(TABLE, {"channel": channel, "agent_id": agent_id, "payload_json": json.dumps(payload, default=str), "received_at": utc_now()})

    async def pull(self, channel: str, agent_id: str, limit: int = 50) -> list[WebhookEvent]:
        """Claim up to ``limit`` unclaimed events for (channel, agent) in arrival order."""
        rows = await self._db.get(TABLE, {"channel": channel, "agent_id": agent_id, "claimed_at": None})
        rows = sorted(rows, key=lambda r: int(r["id"]))[:limit]
        events: list[WebhookEvent] = []
        for row in rows:
            # The claim re-asserts ``claimed_at IS NULL``: two readers (a rolling
            # restart overlapping a draining process) both see the row unclaimed,
            # but only the UPDATE that matches wins; the loser skips it. Both
            # backends return the affected-row count. At-most-once on purpose.
            affected = await self._db.update(TABLE, {"id": row["id"], "claimed_at": None}, {"claimed_at": utc_now()})
            if not affected:
                continue
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            events.append(WebhookEvent(id=int(row["id"]), channel=channel, agent_id=agent_id, payload=payload if isinstance(payload, dict) else {"value": payload}, received_at=row.get("received_at")))
        return events

    async def pending(self, channel: str, agent_id: str) -> int:
        rows = await self._db.get(TABLE, {"channel": channel, "agent_id": agent_id, "claimed_at": None})
        return len(rows)

    async def purge_claimed(self, channel: str, older_than: Any) -> int:
        """Delete CLAIMED rows of ``channel`` claimed before ``older_than`` (the audit window); unclaimed rows are never touched.

        One statement, not fetch-then-loop: the table is append-only under
        load and the sweep must not read the whole history into memory. Raw
        SQL in both dialects (``%s`` placeholders, no quoted identifiers);
        exercised on MySQL by tests/channel/test_webhook_inbox_mysql.py.
        """
        if isinstance(older_than, datetime):
            older_than = older_than.isoformat()  # what both backends store (see _serialize_value)
        cursor_rows = await self._db.execute(
            f"SELECT COUNT(*) AS n FROM {TABLE} WHERE channel = %s AND claimed_at IS NOT NULL AND claimed_at < %s",
            (channel, older_than),
        )
        count = int((cursor_rows[0] or {}).get("n") or 0) if cursor_rows else 0
        if count:
            await self._db.execute(
                f"DELETE FROM {TABLE} WHERE channel = %s AND claimed_at IS NOT NULL AND claimed_at < %s",
                (channel, older_than),
                fetch=False,
            )
        return count


__all__ = ["TABLE", "WebhookEvent", "WebhookInbox"]
