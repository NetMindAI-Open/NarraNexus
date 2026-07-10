"""
@file_name: memory_consolidation_worker.py
@author: NetMind.AI
@date: 2026-06-03
@description: Background consolidation worker for the unified memory system.

Drains the `memory_consolidation_queue` (design §7.4): a turn marks a
(scope, kind) dirty cheaply and synchronously; this worker distils the
accumulated raw memory into observations/summaries OUT of the turn's path.

Triggers (any one fires a scope, only `status='dirty'` rows are eligible):
  - count  : pending_count >= spec.consolidate_threshold   (active burst)
  - idle   : now - last_dirty_at >= IDLE_SECONDS            (conversation settled)
  - cap    : pending_count >= CAP                           (backlog guard)

A scope is set to `processing` while in flight (re-entrancy guard); on success
it returns to `dirty` with pending_count reset; on failure it is isolated as
`failed` (consolidation_failed_at set) so one bad scope never blocks others.

Iron rule #14: this never force-stops or caps an agent_loop — it is purely
opportunistic background work that coalesces whatever has piled up.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context.memory.engine import MemoryEngine
from xyz_agent_context.memory.record import _parse_dt
from xyz_agent_context.memory.spec import get_spec
from xyz_agent_context.utils.cost_tracker import (
    clear_cost_context,
    set_cost_context,
)
from xyz_agent_context.utils.timezone import utc_now

_QUEUE = "memory_consolidation_queue"


class MemoryConsolidationWorker:
    """One worker per process; polls the dirty queue on an interval."""

    POLL_INTERVAL = 30.0   # seconds between polls
    IDLE_SECONDS = 90.0    # quiet-scope flush threshold
    CAP = 20               # hard backlog trigger

    def __init__(self, db_client: Any, *, poll_interval: float = POLL_INTERVAL):
        self._db = db_client
        self.poll_interval = poll_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        # Test seam: the actual consolidation call. Tests patch this; production
        # uses the default below (loads facts + runs the engine).
        self._engine_consolidate = self._default_engine_consolidate

    # ── lifecycle (mirrors services/module_poller.py) ───────────────────────
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[memory.consolidation] worker started")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[memory.consolidation] worker stopped")

    async def _run_loop(self) -> None:
        while self.running:
            try:
                await self._run_one_pass()
            except Exception as e:  # noqa: BLE001 — a pass must never kill the loop
                logger.exception(f"[memory.consolidation] pass failed: {e}")
            await asyncio.sleep(self.poll_interval)

    # ── core ────────────────────────────────────────────────────────────────
    async def _collect_triggered_scopes(self) -> List[Dict[str, Any]]:
        """Return the `dirty` queue rows whose trigger condition is met.
        `processing`/`failed` rows are excluded by the status filter (re-entrancy
        guard + failure isolation)."""
        rows = await self._db.get(_QUEUE, {"status": "dirty"})
        now = utc_now()
        triggered: List[Dict[str, Any]] = []
        for row in rows:
            try:
                spec = get_spec(row["kind"])
            except KeyError:
                continue  # unknown kind — leave it dirty rather than crash
            pending = int(row.get("pending_count") or 0)
            last_dirty = _parse_dt(row.get("last_dirty_at"))
            idle = last_dirty is not None and (now - last_dirty).total_seconds() >= self.IDLE_SECONDS
            if pending >= spec.consolidate_threshold or pending >= self.CAP or idle:
                triggered.append(row)
        return triggered

    async def _run_one_pass(self) -> int:
        triggered = await self._collect_triggered_scopes()
        for row in triggered:
            await self._process_scope(_scope_key(row))
        return len(triggered)

    async def flush_scope(self, *, agent_id: str, scope_type: str, scope_id: str, kind: str) -> None:
        """Force-consolidate one scope now, regardless of triggers (used at
        narrative boundaries — switch/complete)."""
        await self._process_scope(
            {"agent_id": agent_id, "scope_type": scope_type, "scope_id": scope_id, "kind": kind}
        )

    async def _process_scope(self, key: Dict[str, Any]) -> None:
        """Run one scope through consolidation with the processing→dirty/failed
        state machine. Isolated per scope: a failure here never propagates."""
        now = utc_now()
        await self._db.update(_QUEUE, key, {"status": "processing", "updated_at": now})
        try:
            await self._engine_consolidate(**key)
        except Exception as e:  # noqa: BLE001 — isolate the bad scope
            # Systemic LLM/provider failures are platform problems, not
            # content problems — surface them at ERROR so they can never
            # be silent again (incident lesson #4: L2 health is "is it
            # doing useful work", not "is the loop alive").
            from xyz_agent_context.memory._memory_impl.consolidate import SystemicLLMError
            log = logger.error if isinstance(e, SystemicLLMError) else logger.warning
            log(f"[memory.consolidation] scope {key} failed, isolating (facts preserved): {e}")
            await self._db.update(_QUEUE, key, {
                "status": "failed", "consolidation_failed_at": utc_now(), "updated_at": utc_now(),
            })
            return
        await self._db.update(_QUEUE, key, {
            "pending_count": 0, "status": "dirty",
            "last_consolidated_at": utc_now(), "consolidation_failed_at": None, "updated_at": utc_now(),
        })

    async def _inject_owner_credentials(self, agent_id: str) -> None:
        """Resolve the agent OWNER's LLM config into this task's ContextVars.

        The worker runs in the backend lifespan — outside any HTTP request —
        so the auth_middleware ContextVar injection never happens here. On
        cloud that meant every consolidation LLM call fell back to the empty
        machine-global config and 401'd (2026-06-11 P0). Local mode: the
        resolver is a strict no-op and the desktop llm_config.json applies.

        Raises ProviderResolverError subclasses (quota exhausted / no
        provider) — the scope is isolated as failed with facts intact, and
        retried once the owner's provider situation changes.

        Delegates to the shared ``inject_owner_helper_credentials`` (the same
        primitive the narrative updater and Step-5 hooks now use) so the
        clear-first / owner-lookup / resolve sequence lives in exactly one
        place.
        """
        from xyz_agent_context.agent_framework.provider_resolver import (
            inject_owner_helper_credentials,
        )

        await inject_owner_helper_credentials(agent_id, self._db)

    async def _default_engine_consolidate(self, *, agent_id: str, scope_type: str, scope_id: str, kind: str) -> int:
        """Production consolidation: gather the scope's NEW raw units (the
        kind's subtypes, created since the last pass) and EXISTING consolidated
        records, run the engine, then tombstone the consumed raw units — their
        content now lives in the consolidated record, traceable via source_ids.
        """
        await self._inject_owner_credentials(agent_id)
        # Account the LLM tokens this background pass burns. Without this the
        # worker ran OUTSIDE any cost context (only AgentRuntime.run set it),
        # so every consolidation call recorded ZERO — the largest silent hole
        # in token accounting (Phase 0 / module H).
        #
        # RECORD ONLY, NEVER BILL: record_cost's deduct hook fires solely when
        # provider_source=="system" AND current_user_id is set. The worker set
        # neither current_user_id here (_inject_owner_credentials sets
        # provider_source only) — so consolidation is fully accounted yet never
        # charged to the owner's free tier. Do NOT add set_current_user_id in
        # this path: that would silently drain the owner's system quota for
        # background work they never initiated (iron rule #15).
        set_cost_context(agent_id, self._db)
        try:
            spec = get_spec(kind)
            engine = MemoryEngine(self._db, agent_id)
            repo = engine.repo(kind)

            qrow = await self._db.get_one(
                _QUEUE, {"agent_id": agent_id, "scope_type": scope_type, "scope_id": scope_id, "kind": kind}
            )
            last = _parse_dt(qrow.get("last_consolidated_at")) if qrow else None

            scoped = await repo.query(agent_id=agent_id, scope_type=scope_type, scope_id=scope_id, live_only=True)
            raw_subtypes = set(spec.subtypes)
            # Raw units carry a subtype (world/experience…); consolidated records do
            # not. New = raw units created after the last consolidation boundary.
            new_facts = [
                r for r in scoped
                if r.subtype in raw_subtypes and (last is None or (r.created_at and r.created_at > last))
            ]
            existing = [r for r in scoped if r.subtype not in raw_subtypes]
            if not new_facts:
                return 0

            changed = await engine.consolidate(
                kind, scope_type=scope_type, scope_id=scope_id, new_facts=new_facts, existing=existing
            )
            for r in new_facts:  # consumed — content is now in a consolidated record
                await repo.tombstone(r.record_id)
            return changed
        finally:
            # Sequential multi-tenant worker: clear so the next scope never
            # inherits this agent's (agent_id, db) — mirrors clear_user_config.
            clear_cost_context()


def _scope_key(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent_id": row["agent_id"],
        "scope_type": row["scope_type"],
        "scope_id": row.get("scope_id") or "",
        "kind": row["kind"],
    }
