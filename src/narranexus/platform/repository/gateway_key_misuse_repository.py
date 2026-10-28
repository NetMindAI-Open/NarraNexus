"""
@file_name: gateway_key_misuse_repository.py
@author: Bin Liang
@date: 2026-08-19
@description: Data-access writer for gateway_key_misuse — the record of abnormal
/ unauthorized use of a gateway key, for security monitoring.

One row per event. The row records a ``user_id`` that the CALLER already
reverse-resolved for the offending key (the gateway is the authority on which
identity the key is bound to) — this layer treats it as an opaque, already
authoritative value and stores it verbatim. It parses nothing, and knows
nothing about *how* the id was resolved.

Unlike the advisory ``ban_audit`` trail, this row IS the payload, not a side
note: the security monitor reads gateway_key_misuse read-only to drive its
response ladder, so a dropped row is a missed enforcement, not a lost
breadcrumb. ``record`` therefore does NOT swallow write failures — it lets them
surface so the caller (and the endpoint's 5xx) reflect that the authoritative
record was not persisted.
"""
from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from narranexus.platform.utils.db.dialect_errors import is_unique_violation


class GatewayKeyMisuseRepository:
    """Append-only writer for authoritative gateway-key misuse records."""

    TABLE = "gateway_key_misuse"

    # An event enters the ladder as 'pending'; the monitor/dispositioner advances
    # it (e.g. to an enforced/alerted terminal state) — this writer never sets
    # anything other than the initial state.
    STATUS_PENDING = "pending"

    def __init__(self, db_client):
        # Untyped on purpose (mirrors BanAuditRepository / ServiceAuditRepository):
        # the async DB client is injected; importing its type here would only add
        # a load-order coupling for no benefit.
        self._db = db_client

    async def record(
        self,
        *,
        user_id: Optional[str] = None,
        run_id: Optional[str] = None,
        key_hash: Optional[str] = None,
        caller_ip: Optional[str] = None,
        caller_ua: Optional[str] = None,
        model: Optional[str] = None,
        hit_at: Optional[str] = None,
    ) -> Tuple[int, bool]:
        """Persist one authoritative event; return ``(row_id, deduped)``.

        ``deduped`` is False on a fresh insert and True when an at-least-once
        retry collapsed onto an existing row — so the caller can tell an
        idempotent retry apart from a first write (and observe the retry rate)
        instead of a status flag that is always True.

        ``user_id`` is the caller's already-reverse-resolved attribution, or
        None for an unresolved event (stored as an alert-only row; the response
        ladder never fires on a NULL id). None-valued fields fall through to the
        column defaults at the DB facade, so an alert-only row still gets its
        ``disposition_status='pending'`` and timestamps.

        ``hit_at`` is the authoritative time the misuse occurred, already
        normalised by the endpoint to the DATETIME(6) contract
        ``YYYY-MM-DD HH:MM:SS.ffffff`` (UTC). When given it is the idempotency
        anchor: the table has a UNIQUE (key_hash, hit_at) index, so a
        write-succeeded-but-response-timed-out retry of the same event collapses
        to the SAME row instead of a duplicate (a hard signal is acted on once; a
        duplicate row would be double-acted). On that collision we return the
        existing row's id with ``deduped=True`` — an idempotent success, NOT a
        swallowed failure. Any OTHER write error still surfaces (a dropped row is
        a missed enforcement). When ``hit_at`` is omitted the column default
        (insert time) applies and no dedup is possible. The reverse-lookup keys
        on the SAME normalised ``hit_at`` that was written, so it always finds the
        row it just collided with.
        """
        row = {
            "user_id": user_id,
            "run_id": run_id,
            "key_hash": key_hash,
            "caller_ip": caller_ip,
            "caller_ua": caller_ua,
            "model": model,
            "disposition_status": self.STATUS_PENDING,
        }
        if hit_at is not None:
            row["hit_at"] = hit_at

        try:
            return await self._db.insert(self.TABLE, row), False
        except Exception as e:  # noqa: BLE001 — re-raised unless it is THE dedup race
            is_dupe = is_unique_violation(e)
            # Only a (key_hash, hit_at) collision — i.e. an at-least-once retry of
            # the same event — is idempotent. Both values are non-null in that
            # case (the unique index does not fire on a NULL key_hash), so we can
            # look the existing row back up and return its id. Anything else
            # (connection lost, disk full, a real write failure) MUST surface so
            # the endpoint 5xx's and the caller knows the record did not persist.
            if is_dupe and key_hash is not None and hit_at is not None:
                existing = await self._db.get_one(
                    self.TABLE, {"key_hash": key_hash, "hit_at": hit_at}
                )
                if existing is not None:
                    logger.info(
                        "[gateway-key-misuse] idempotent retry collapsed to "
                        f"existing row id={existing.get('id')}"
                    )
                    return int(existing["id"]), True
            raise
