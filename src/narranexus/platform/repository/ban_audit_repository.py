"""
@file_name: ban_audit_repository.py
@author: Bin Liang
@date: 2026-08-13
@description: Append-only audit trail for administrative account-state changes.

One row per action (suspend / reinstate) applied to a user's account state.
Generalised from the ``service_audit`` recorder: a thin best-effort writer over
one table, keyed by ``user_id``.

Deliberately neutral and policy-free. ``reason`` and ``evidence_ref`` are
OPAQUE strings the caller supplies — this layer never interprets, categorises,
or validates them, and knows nothing about *why* an account state changed. It
only records THAT it changed, by whom (``actor``), and when.

**Best-effort writes** — ``record`` NEVER raises into the caller. The audit
row is advisory: losing one must not fail the state change the operator asked
for (the ``users.status`` update is the source of truth; this is the trail).
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

ACTION_SUSPEND = "suspend"
ACTION_REINSTATE = "reinstate"
ACTION_WARN = "warn"


class BanAuditRepository:
    """Append-only log of account-state changes, shared across callers."""

    TABLE = "ban_audit"

    def __init__(self, db_client):
        # Untyped on purpose (mirrors ServiceAuditRepository): the async DB
        # client is injected; importing its type here would only add a
        # load-order coupling for no benefit.
        self._db = db_client

    async def record(
        self,
        user_id: str,
        action: str,
        *,
        reason: Optional[str] = None,
        evidence_ref: Optional[str] = None,
        actor: Optional[str] = None,
        prev_status: Optional[str] = None,
    ) -> None:
        """Best-effort audit write. Never raises into the caller.

        ``prev_status`` records the account state the row was in immediately
        before this action (opaque; a plain ``users.status`` value), so the
        trail shows what a suspend replaced / what a reinstate reverted from.
        """
        try:
            await self._db.insert(
                self.TABLE,
                {
                    "user_id": user_id,
                    "action": action,
                    "reason": reason,
                    "evidence_ref": evidence_ref,
                    "actor": actor,
                    "prev_status": prev_status,
                },
            )
        except Exception as e:  # noqa: BLE001 — audit writes are advisory
            logger.warning(
                f"BanAudit write failed ({user_id}/{action}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory)"
            )

    async def history(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return a user's account-state changes, newest first."""
        try:
            return await self._db.get(
                self.TABLE,
                {"user_id": user_id},
                limit=limit,
                order_by="id DESC",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"BanAudit history() failed: {type(e).__name__}: {e}")
            return []
