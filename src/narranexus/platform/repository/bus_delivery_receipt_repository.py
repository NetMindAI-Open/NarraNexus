"""
@file_name: bus_delivery_receipt_repository.py
@author:
@date: 2026-09-09
@description: The owner of `bus_delivery_receipts` — the per-(message,
recipient) delivery ledger a bus sender can be shown.

A bus send used to be a fire-and-forget insert: "success" meant the row
existed, and everything after — the recipient's trigger crashing on it three
times, or its turn coming back empty — was visible only in the recipient
OWNER's inbox. The sender agent, which had just promised its user that work
was under way, learned nothing (upstream NetMindAI-Open/NarraNexus#106).

The receipt is one row per (message_id, to_agent), updated in place as the
message moves through its life:

    accepted   send tool: queued, recipient reachable
    held       send tool: queued, but the recipient's circuit breaker is
               paused/cooling — it will not run until that clears
    processed  trigger: the turn ran and reached someone (peer reply / tool)
    relayed    trigger: the turn produced owner-facing text only — the
               recipient relayed to ITS owner, the sender got no reply
    silent     trigger: the turn ran and reached nobody
    failed     trigger: the turn raised (retry pending; `attempts` counts)
    dropped    trigger: poison threshold reached, no more retries

`content_key` is the sha256 of the message content the recipient's turn was
built from. It exists for one question — "has this recipient ALREADY gone
silent on this exact content in this channel?" — which is how the trigger
tells a resend from a fresh message and stops waking the sender a second time
for the same silence (the 8/31 "This turn ended without delivering a reply"
ping-pong).

Plain class (composite key, upsert + two reads); dialect-safe by construction —
`get_one` / `get` / `insert` / `update` only, no hand-written SQL.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, Optional

from datetime import timedelta

from loguru import logger

from narranexus.platform.utils.timezone import coerce_utc, to_datetime6_literal, utc_now

RECEIPT_ACCEPTED = "accepted"
RECEIPT_HELD = "held"
RECEIPT_PROCESSED = "processed"
RECEIPT_RELAYED = "relayed"
RECEIPT_SILENT = "silent"
RECEIPT_FAILED = "failed"
RECEIPT_DROPPED = "dropped"

def content_key(content: str) -> str:
    """Stable fingerprint of what a recipient was asked. Whitespace-normalised
    so a model that re-flows the same text on resend still matches."""
    normalised = " ".join((content or "").split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class BusDeliveryReceiptRepository:
    TABLE = "bus_delivery_receipts"

    def __init__(self, db):
        # Untyped on purpose (the family convention): the async DB client is
        # injected; naming its type here would only add a load-order coupling.
        self._db = db

    async def upsert(
        self,
        *,
        message_id: str,
        to_agent: str,
        channel_id: str,
        from_agent: str,
        status: str,
        reason: Optional[str] = None,
        attempts: Optional[int] = None,
        content_key: Optional[str] = None,
    ) -> None:
        """Insert-or-update the receipt for (message_id, to_agent).

        Only the fields given are written on an existing row (plus
        `updated_at`); `channel_id` / `from_agent` are identity and set on
        insert. `reason` must already be redacted by the caller.
        """
        key = {"message_id": message_id, "to_agent": to_agent}
        now = utc_now()
        data: Dict[str, Any] = {"status": status, "updated_at": now}
        if reason is not None:
            data["reason"] = reason
        if attempts is not None:
            data["attempts"] = int(attempts)
        if content_key is not None:
            data["content_key"] = content_key
        updated = await self._db.update(self.TABLE, key, data)
        if not updated:
            await self._db.insert(self.TABLE, {
                **key,
                "channel_id": channel_id,
                "from_agent": from_agent,
                "attempts": int(attempts or 0),
                "created_at": now,
                **data,
            })

    async def get(self, message_id: str, to_agent: str) -> Optional[Dict[str, Any]]:
        return await self._db.get_one(
            self.TABLE, {"message_id": message_id, "to_agent": to_agent}
        )

    async def prior_outcome(
        self,
        *,
        channel_id: str,
        to_agent: str,
        key: str,
        status: str,
        exclude_message_ids: Iterable[str],
        within_seconds: float,
    ) -> bool:
        """Did `to_agent` already reach `status` on this exact content in this
        channel, on a message OUTSIDE `exclude_message_ids` (the current
        batch's own rows), within the window?

        The guard behind "wake the sender once, not every time" for both a
        silent turn (`silent`) and a dropped message (`dropped`). Windowed on
        `updated_at`, never permanent: a daily check-in that went unanswered
        once must still be able to wake the sender next month, and a recipient
        that was fixed and broke again must be reported anew.
        """
        rows = await self._db.get(
            self.TABLE,
            {"channel_id": channel_id, "to_agent": to_agent, "content_key": key},
        )
        floor = utc_now() - timedelta(seconds=within_seconds)
        excluded = set(exclude_message_ids)
        for r in rows or []:
            if r.get("status") != status or r.get("message_id") in excluded:
                continue
            seen = coerce_utc(r.get("updated_at"))
            if seen is not None and seen >= floor:
                return True
        return False

    async def cleanup_older_than_days(self, days: int) -> int:
        """Delete receipts not touched for ``days``. Returns rows deleted
        (best-effort; 0 on driver error).

        A receipt is a delivery-time fact; once every window that reads it
        (`prior_outcome`'s) has closed it is history, and this table would
        otherwise grow 1:1 with `bus_messages` forever. Run by
        ``MessageBusTrigger._maybe_run_steer_cleanup`` daily, with a retention
        far above the guard window.
        """
        cutoff = to_datetime6_literal(utc_now() - timedelta(days=days))
        try:
            result = await self._db.execute(
                f"DELETE FROM {self.TABLE} WHERE updated_at < %s",
                params=(cutoff,),
                fetch=False,
            )
            return int(result) if isinstance(result, (int, float)) else 0
        except Exception as e:  # noqa: BLE001 — retention is best-effort
            logger.warning(f"[bus-receipt] cleanup({days}): {type(e).__name__}: {e}")
            return 0
