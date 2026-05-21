"""
@file_name: invite_code_repository.py
@author: NarraNexus
@date: 2026-05-14
@description: InviteCode repository — data access for the registration gate.

Backs the cloud-mode invite-code mechanism (Mode B: auto-issue + global cap +
waitlist). Replaces the single global INVITE_CODE env var.

Key invariant: `consume()` is a single conditional UPDATE — it flips a code
issued → used atomically, so two concurrent registrations racing on one code
cannot both win. The caller checks the affected-row count.

See drafts/logs/invite_code_2026_05_14.md for the full design.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .base import BaseRepository
from xyz_agent_context.schema.invite_code_schema import InviteCode
from xyz_agent_context.utils.invite_code_gen import generate_code
from xyz_agent_context.utils.timezone import utc_now

# Codes that count against the auto-issue cap (they represent a real or
# pending account). `waitlisted` and `revoked` do NOT count.
_ACTIVE_STATUSES = ("issued", "used")

_CREATE_MAX_RETRIES = 8


def _now_str() -> str:
    """Timestamp string matching the schema's `(datetime('now'))` default —
    `YYYY-MM-DD HH:MM:SS`, accepted by both SQLite TEXT and MySQL DATETIME."""
    return utc_now().strftime("%Y-%m-%d %H:%M:%S")


class InviteCodeRepository(BaseRepository[InviteCode]):
    table_name = "invite_codes"
    id_field = "code"

    # ── reads ────────────────────────────────────────────────────────────

    async def get_by_code(self, code: str) -> Optional[InviteCode]:
        row = await self._db.get_one(self.table_name, {"code": code})
        return self._row_to_entity(row) if row else None

    async def list_for_email(self, email: str) -> List[InviteCode]:
        """Every row ever created for this email, newest first."""
        rows = await self._db.get(
            self.table_name, {"email": email}, order_by="created_at DESC"
        )
        return [self._row_to_entity(r) for r in rows]

    async def list_all(self, status: Optional[str] = None) -> List[InviteCode]:
        filters: Dict[str, Any] = {"status": status} if status else {}
        rows = await self._db.get(
            self.table_name, filters, order_by="created_at DESC"
        )
        return [self._row_to_entity(r) for r in rows]

    async def count_active(self) -> int:
        """Number of codes that count against the auto-issue cap
        (status issued or used)."""
        total = 0
        for st in _ACTIVE_STATUSES:
            rows = await self._db.get(self.table_name, {"status": st})
            total += len(rows)
        return total

    # ── writes ───────────────────────────────────────────────────────────

    async def create(
        self,
        email: str,
        status: str = "issued",
        source: str = "website",
    ) -> InviteCode:
        """Generate a unique code and insert one row.

        Uniqueness is enforced by the DB's UNIQUE(code) constraint; on the
        (astronomically rare) collision we regenerate and retry. `issued_at`
        is stamped now when status == 'issued'; a waitlisted row gets it
        later, on promote().
        """
        last_err: Optional[Exception] = None
        for _ in range(_CREATE_MAX_RETRIES):
            code = generate_code()
            payload: Dict[str, Any] = {
                "code": code,
                "email": email,
                "status": status,
                "source": source,
                "email_sent": 0,
                "issued_at": _now_str() if status == "issued" else None,
            }
            try:
                await self._db.insert(self.table_name, payload)
                row = await self._db.get_one(self.table_name, {"code": code})
                return self._row_to_entity(row) if row else InviteCode(**payload)
            except Exception as e:  # noqa: BLE001 — retry on unique collision
                last_err = e
                logger.warning(
                    "InviteCodeRepository.create: insert failed for {}, "
                    "retrying with a fresh code: {}",
                    code,
                    e,
                )
        raise RuntimeError(
            f"failed to create invite code after {_CREATE_MAX_RETRIES} retries"
        ) from last_err

    async def mark_email_sent(self, code: str, sent: bool) -> None:
        await self._db.update(
            self.table_name, {"code": code}, {"email_sent": 1 if sent else 0}
        )

    async def consume(self, code: str, user_id: str) -> bool:
        """Atomically flip a code issued → used. Returns True iff this call
        is the one that consumed it (affected exactly one row).

        Single conditional UPDATE — the `status = 'issued'` filter is the
        race guard: a second concurrent caller updates zero rows.
        """
        affected = await self._db.update(
            self.table_name,
            {"code": code, "status": "issued"},
            {
                "status": "used",
                "used_at": _now_str(),
                "used_by_user_id": user_id,
            },
        )
        return affected == 1

    async def revert_consume(self, code: str) -> None:
        """Undo a consume() — used → issued. Called when user-row insertion
        fails after the code was already consumed, so the code isn't burned."""
        await self._db.update(
            self.table_name,
            {"code": code, "status": "used"},
            {"status": "issued", "used_at": None, "used_by_user_id": None},
        )

    async def promote(self, code: str) -> bool:
        """Admin: waitlisted → issued. Returns True iff a row was promoted."""
        affected = await self._db.update(
            self.table_name,
            {"code": code, "status": "waitlisted"},
            {"status": "issued", "issued_at": _now_str()},
        )
        return affected == 1

    async def revoke(self, code: str) -> bool:
        """Admin: issued|waitlisted → revoked. A used code cannot be revoked
        (the account already exists). Returns True iff a row was revoked."""
        affected = 0
        for st in ("issued", "waitlisted"):
            affected += await self._db.update(
                self.table_name,
                {"code": code, "status": st},
                {"status": "revoked"},
            )
        return affected >= 1

    # ── entity mapping ───────────────────────────────────────────────────

    def _row_to_entity(self, row: Dict[str, Any]) -> InviteCode:
        return InviteCode(
            id=row.get("id"),
            code=row["code"],
            email=row["email"],
            status=row["status"],
            source=row.get("source") or "website",
            email_sent=bool(row.get("email_sent")),
            created_at=row.get("created_at"),
            issued_at=row.get("issued_at"),
            used_at=row.get("used_at"),
            used_by_user_id=row.get("used_by_user_id"),
        )

    def _entity_to_row(self, entity: InviteCode) -> Dict[str, Any]:
        return {
            "code": entity.code,
            "email": entity.email,
            "status": entity.status,
            "source": entity.source,
            "email_sent": 1 if entity.email_sent else 0,
            "issued_at": entity.issued_at,
            "used_at": entity.used_at,
            "used_by_user_id": entity.used_by_user_id,
        }
