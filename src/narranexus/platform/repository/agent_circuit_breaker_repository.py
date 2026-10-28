"""
@file_name: agent_circuit_breaker_repository.py
@author:
@date: 2026-07-13
@description: Data access for the real-time-layer Agent circuit-breaker.

CRUD over ``instance_agent_circuit_breaker`` (one row per agent_id). The
breaker service owns all the escalation logic; this layer only reads/writes
rows. ``upsert_state`` is the workhorse — a partial, agent_id-keyed
insert-or-update that stamps ``updated_at``.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from narranexus.platform.schema import AgentCircuitBreaker, CbStatus
from narranexus.platform.utils.timezone import utc_now

from .base import BaseRepository


def _generate_probe_token() -> str:
    """A short random value, unique enough to serve as a compare-and-swap
    key (not a security token — never compared against untrusted input)."""
    return secrets.token_hex(16)


class AgentCircuitBreakerRepository(BaseRepository[AgentCircuitBreaker]):
    """Repository for Agent circuit-breaker state."""

    table_name = "instance_agent_circuit_breaker"
    id_field = "agent_id"

    async def get(self, agent_id: str) -> Optional[AgentCircuitBreaker]:
        """Return the breaker row for an agent, or None if it has none yet."""
        row = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        return self._row_to_entity(row) if row else None

    async def upsert_state(self, agent_id: str, updates: Dict[str, Any]) -> None:
        """Insert-or-update the agent's breaker row with ``updates``.

        Keyed on agent_id; always refreshes ``updated_at``. A partial write —
        only the keys in ``updates`` (plus updated_at) are touched on an
        existing row.
        """
        data = dict(updates)
        data["updated_at"] = utc_now()
        existing = await self._db.get_one(self.table_name, {"agent_id": agent_id})
        if existing:
            await self._db.update(self.table_name, {"agent_id": agent_id}, data)
        else:
            data["agent_id"] = agent_id
            await self._db.insert(self.table_name, data)

    async def find_by_status(self, status: str) -> List[AgentCircuitBreaker]:
        """All breaker rows currently in the given ``cb_status``."""
        rows = await self._db.get(self.table_name, filters={"cb_status": status})
        return [self._row_to_entity(r) for r in rows if r]

    async def try_claim_probe(
        self,
        agent_id: str,
        from_status: str,
        expected_probe_token: Optional[str],
        grant_until: datetime,
    ) -> Optional[str]:
        """Atomically transition ``from_status`` -> PROBING, granting the
        caller the single half-open probe turn.

        The compare-and-swap key is ``probe_token``, not ``cb_status`` alone.
        Filtering on ``cb_status=from_status`` is a CAS only while the write
        changes that column: the first PAUSED->PROBING claim does, but the
        stale-PROBING self-heal re-claims FROM "probing" TO "probing", so a
        status-only filter kept matching for every later racer and each one
        "won". ``probe_token`` is a fresh random value on every successful
        claim and NULL whenever the row is written back to PAUSED / COOLING /
        ACTIVE, so it always differs before vs. after a real claim, on both
        branches.

        ``expected_probe_token`` is whatever the caller most recently READ:
        None for a fresh pause (the filter becomes ``probe_token IS NULL``),
        the row's current token for a stale-PROBING reclaim. ``grant_until``
        re-stamps ``cooldown_until`` as the probe's own expiry.

        Dialect note: aiomysql's rowcount counts CHANGED rows, not matched
        ones. That is fine here because ``probe_token`` is always new, but it
        means a "just update cb_status" simplification would silently never
        win on MySQL for the probing->probing branch — the MySQL twin
        (``tests/agent_framework/test_agent_circuit_breaker_probe_mysql.py``)
        pins this.

        Returns the NEW probe_token this caller now owns if it won the race,
        or None if it lost.
        """
        new_token = _generate_probe_token()
        now = utc_now()
        rowcount = await self._db.update(
            self.table_name,
            {
                "agent_id": agent_id,
                "cb_status": from_status,
                "probe_token": expected_probe_token,
            },
            {
                "cb_status": CbStatus.PROBING.value,
                "probe_token": new_token,
                # A fresh claim has no claimant run yet; the winner binds its
                # own via bind_probe_run once its events row exists.
                "probe_run_id": None,
                "cooldown_until": grant_until,
                "updated_at": now,
            },
        )
        return new_token if rowcount > 0 else None

    async def settle_probe(
        self, agent_id: str, probe_token: str, updates: Dict[str, Any]
    ) -> bool:
        """Write ``updates`` ONLY if the row is still PROBING under
        ``probe_token`` — the settlement half of the claim CAS.

        Only the turn that won ``try_claim_probe`` holds the token, so a
        settlement can never be written by a turn that did not claim (an
        unrelated long run, an ungated entry point) nor land on a claim that
        was already settled, reset by the owner, or re-claimed after going
        stale. ``updates`` must move the row out of PROBING and clear
        ``probe_token`` (every caller writes PAUSED/ACTIVE with a NULL
        token), which is also what makes the MySQL changed-rows rowcount
        reliable here: the token column always changes on a win.

        Returns True iff this call settled the claim.
        """
        data = dict(updates)
        data["updated_at"] = utc_now()
        rowcount = await self._db.update(
            self.table_name,
            {
                "agent_id": agent_id,
                "cb_status": CbStatus.PROBING.value,
                "probe_token": probe_token,
            },
            data,
        )
        return rowcount > 0

    async def bind_probe_run(
        self, agent_id: str, probe_token: str, run_id: str
    ) -> bool:
        """Stamp ``probe_run_id`` ONLY if the row is still PROBING under
        ``probe_token`` — the claimant naming its own run. Unlike
        ``settle_probe`` it leaves the row PROBING and the token in place.

        Returns True iff a row changed. On MySQL (aiomysql rowcount = CHANGED
        rows) re-binding the same run id still changes ``updated_at``, but a
        False here is informational only: callers never branch on it as a
        CAS verdict — a lost claim simply is not bound.
        """
        rowcount = await self._db.update(
            self.table_name,
            {
                "agent_id": agent_id,
                "cb_status": CbStatus.PROBING.value,
                "probe_token": probe_token,
            },
            {"probe_run_id": run_id, "updated_at": utc_now()},
        )
        return rowcount > 0

    async def find_paused(self) -> List[AgentCircuitBreaker]:
        """All agents currently in PAUSED state (any reason)."""
        return await self.find_by_status(CbStatus.PAUSED.value)

    def _row_to_entity(self, row: Dict[str, Any]) -> AgentCircuitBreaker:
        # Pydantic ignores the extra ``id`` column and coerces ISO strings /
        # enum strings into the model's types.
        return AgentCircuitBreaker(**row)

    def _entity_to_row(self, entity: AgentCircuitBreaker) -> Dict[str, Any]:
        row = entity.model_dump()
        row.pop("created_at", None)  # DB default handles first insert
        return row
