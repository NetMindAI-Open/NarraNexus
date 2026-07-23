"""
@file_name: quota_repository.py
@author: Bin Liang
@date: 2026-04-16
@description: Data-access layer for the `user_quotas` table.

Atomic UPDATEs for deduct/grant so concurrent LLM calls from one user
do not lose counts via read-modify-write races.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger

from .base import BaseRepository
from .service_audit_repository import EVENT_ERROR, ServiceAuditRepository
from xyz_agent_context.schema.quota_schema import Quota, QuotaStatus


class QuotaRepository(BaseRepository[Quota]):
    table_name = "user_quotas"
    id_field = "user_id"  # logical key; surrogate PK `id` is table-internal

    async def get_by_user_id(self, user_id: str) -> Optional[Quota]:
        row = await self._db.get_one(self.table_name, {"user_id": user_id})
        return self._row_to_entity(row) if row else None

    async def create(
        self,
        user_id: str,
        initial_input_tokens: int,
        initial_output_tokens: int,
    ) -> Quota:
        now = datetime.now(timezone.utc)
        entity = Quota(
            user_id=user_id,
            initial_input_tokens=initial_input_tokens,
            initial_output_tokens=initial_output_tokens,
            created_at=now,
            updated_at=now,
        )
        await self._db.insert(self.table_name, self._entity_to_row(entity))
        fetched = await self.get_by_user_id(user_id)
        assert fetched is not None, f"insert of {user_id} failed silently"
        return fetched

    async def atomic_deduct(
        self,
        user_id: str,
        input_delta: int,
        output_delta: int,
        cost_record_id: Optional[int] = None,
        provider_source: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """Atomically bump the running total, then write one audit ledger row.
        Flips status to 'exhausted' when either dimension's post-update
        remaining is <= 0.

        The ledger is what makes a wrong charge auditable and an exact refund
        computable — without it, `used_*` is an opaque scalar with no provenance.

        Why NOT one DB transaction: this method is a concurrent hot path (many
        LLM calls deduct against the shared db client at once). The client's
        transaction() primitive holds a SINGLE shared connection on the client
        instance, so concurrent transactions collide — and on SQLite there is
        only ever one connection, making concurrent multi-statement transactions
        structurally impossible. Wrapping the two writes in transaction() would
        reintroduce the exact lost-update race the single-UPDATE design exists to
        prevent. So the two writes are ordered, not atomic — and the ORDER is
        deliberate:

          1. The single, concurrency-safe UPDATE bumps the total FIRST. This is a
             spend-control gate: the counter moving is the primary invariant.
          2. The ledger row is AUDIT, written best-effort AFTER. A ledger-write
             failure must never SKIP the deduction — doing the ledger first and
             letting it raise would drop the whole charge on any persistent ledger
             fault (unmigrated process, disk/lock error), i.e. silent free-tier
             consumption with a frozen `used_*` — the exact shape of the
             2026-04-22 prod incident. So a ledger failure is logged AND recorded
             to `service_audit` (durable across container restarts, per
             incident-lesson #5), never raised.

        The ledger write is gated on the UPDATE actually matching a row
        (`affected`): if the user has no `user_quotas` row nothing was charged, so
        no ledger row is written (avoids ledger > total). The only divergence is a
        hard crash between the two writes (charged, ledger missing): conservative
        and reconcilable — `used_* >= SUM(quota_deductions)`, and cost_records
        (which already carries user_id) is the secondary attribution backstop.

        Comparisons are written additively (`used + delta >= cap`) rather
        than subtractively (`cap - used - delta <= 0`). All six operands
        are BIGINT UNSIGNED in MySQL, so any intermediate that could go
        negative aborts the whole UPDATE with error 1690. The additive
        form only adds UNSIGNED to UNSIGNED on each side of the
        comparison, which can never underflow.
        """
        # Nothing to deduct. Guard here (not only in QuotaService.deduct) so the
        # "affected" gate below never depends on cross-dialect rowcount semantics
        # for a zero-delta call: MySQL reports changed-rows, SQLite matched-rows,
        # so a stray atomic_deduct(u, 0, 0) would otherwise write a zero-value
        # ledger row on SQLite but not MySQL. This is a public method — keep its
        # invariant self-contained.
        if input_delta <= 0 and output_delta <= 0:
            return

        # 1) Running-total UPDATE FIRST — single atomic, concurrency-safe statement.
        sql = f"""
        UPDATE {self.table_name}
        SET used_input_tokens  = used_input_tokens  + %s,
            used_output_tokens = used_output_tokens + %s,
            status = CASE
              WHEN (used_input_tokens + %s)
                   >= (initial_input_tokens + granted_input_tokens)
                OR (used_output_tokens + %s)
                   >= (initial_output_tokens + granted_output_tokens)
              THEN 'exhausted'
              ELSE status
            END
        WHERE user_id = %s
        """
        affected = await self._db.execute(
            sql,
            params=(input_delta, output_delta, input_delta, output_delta, user_id),
            fetch=False,
        )
        # 2) Audit ledger, best-effort (see docstring). Skip if the UPDATE matched
        #    no row — nothing was charged, so nothing to record.
        #    None-valued optional columns are filtered by db.insert and fall back
        #    to their NULL / default, which is exactly what we want.
        if affected:
            try:
                await self._db.insert(
                    "quota_deductions",
                    {
                        "user_id": user_id,
                        "input_tokens": input_delta,
                        "output_tokens": output_delta,
                        "cost_record_id": cost_record_id,
                        "provider_source": provider_source,
                        "model": model,
                        "agent_id": agent_id,
                    },
                )
            except Exception as e:
                # Charge already applied; never undo/skip it for an audit fault.
                # Leave a durable trail (survives container restart) plus a log.
                # Use ServiceAuditRepository.record (never raises, JSON-serializes
                # detail via _to_detail) so the row is actually readable by the
                # System page's _parse_detail — a hand-written f-string detail
                # would be dropped as non-JSON. Event vocab is
                # started/stopped/heartbeat/error, so the subtype lives in
                # detail.reason under EVENT_ERROR.
                logger.exception(
                    f"quota_deductions ledger write failed for {user_id}: {e}"
                )
                await ServiceAuditRepository(self._db).record(
                    "quota",
                    EVENT_ERROR,
                    {
                        "reason": "ledger_write_failed",
                        "user_id": user_id,
                        "input": input_delta,
                        "output": output_delta,
                        "cost_record_id": cost_record_id,
                        "error": repr(e),
                    },
                )

    async def atomic_grant(
        self, user_id: str, input_delta: int, output_delta: int
    ) -> None:
        """Atomic UPDATE. Reactivates an exhausted user when the grant lifts
        remaining above zero in both dimensions.

        Reactivation condition is additive for the same reason as
        ``atomic_deduct``: when ``used`` already exceeds ``cap + delta``
        (the grant is too small to cover the debt), a subtractive form
        would underflow BIGINT UNSIGNED and roll the whole UPDATE back,
        silently losing the granted credit.
        """
        sql = f"""
        UPDATE {self.table_name}
        SET granted_input_tokens  = granted_input_tokens  + %s,
            granted_output_tokens = granted_output_tokens + %s,
            status = CASE
              WHEN status = 'exhausted'
                AND used_input_tokens
                    < (initial_input_tokens + granted_input_tokens + %s)
                AND used_output_tokens
                    < (initial_output_tokens + granted_output_tokens + %s)
              THEN 'active'
              ELSE status
            END
        WHERE user_id = %s
        """
        await self._db.execute(
            sql,
            params=(input_delta, output_delta, input_delta, output_delta, user_id),
            fetch=False,
        )

    async def set_preference(
        self, user_id: str, prefer_system_override: bool
    ) -> None:
        """Atomic UPDATE of the user-choice toggle."""
        sql = f"""
        UPDATE {self.table_name}
        SET prefer_system_override = %s
        WHERE user_id = %s
        """
        await self._db.execute(
            sql,
            params=(1 if prefer_system_override else 0, user_id),
            fetch=False,
        )

    async def disable_if_enabled(self, user_id: str) -> bool:
        """Atomically turn the free-tier preference OFF, but ONLY if it is
        currently ON. Returns True iff THIS call performed the 1→0 transition.

        The ``WHERE prefer_system_override = 1`` guard makes the write a
        compare-and-swap: under concurrent requests exactly one caller sees the
        row still ON and flips it (affected rows = 1); everyone else finds it
        already OFF (affected rows = 0). The single winner is the one allowed
        to fire a one-time side-effect (the #48 auto-switch notice) without a
        separate lock. See ``QuotaService.disable_preference_if_enabled``.
        """
        sql = f"""
        UPDATE {self.table_name}
        SET prefer_system_override = 0
        WHERE user_id = %s AND prefer_system_override = 1
        """
        affected = await self._db.execute(sql, params=(user_id,), fetch=False)
        return bool(affected)

    def _row_to_entity(self, row: Dict[str, Any]) -> Quota:
        return Quota(
            user_id=row["user_id"],
            initial_input_tokens=row["initial_input_tokens"],
            initial_output_tokens=row["initial_output_tokens"],
            used_input_tokens=row["used_input_tokens"],
            used_output_tokens=row["used_output_tokens"],
            granted_input_tokens=row["granted_input_tokens"],
            granted_output_tokens=row["granted_output_tokens"],
            status=QuotaStatus(row["status"]),
            prefer_system_override=bool(row.get("prefer_system_override", 0)),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def _entity_to_row(self, entity: Quota) -> Dict[str, Any]:
        return {
            "user_id": entity.user_id,
            "initial_input_tokens": entity.initial_input_tokens,
            "initial_output_tokens": entity.initial_output_tokens,
            "used_input_tokens": entity.used_input_tokens,
            "used_output_tokens": entity.used_output_tokens,
            "granted_input_tokens": entity.granted_input_tokens,
            "granted_output_tokens": entity.granted_output_tokens,
            "status": entity.status.value,
            "prefer_system_override": 1 if entity.prefer_system_override else 0,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
