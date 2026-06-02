"""
@file_name: service_audit.py
@author: Bin Liang
@date: 2026-05-29
@description: Reusable L2 observability helper for background loops.

A long-running loop wires one ``ServiceAuditor``:

    audit = ServiceAuditor("job_trigger")
    await audit.started({"poll_interval": 5})
    while running:
        await do_work()
        await audit.heartbeat({"enqueued_total": n})   # throttled, cheap
    await audit.stopped()

Lifecycle events (started/stopped/error) write immediately;
``heartbeat()`` is throttled (default 60s) so a 5s poll loop does not
spam the DB, and carries cumulative work counters so a *stale* heartbeat
(old row, frozen counter) distinguishes "loop wedged" from "loop idle
but alive". All writes are best-effort and never raise into the caller —
the observer must not break the observed. See
``repository/service_audit_repository`` for the rationale.

The DB client is lazily acquired on first write via ``get_db_client()``
so constructing a ServiceAuditor at import/init time is free and safe.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.repository.service_audit_repository import (
    ServiceAuditRepository,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_HEARTBEAT,
    EVENT_ERROR,
)


class ServiceAuditor:
    def __init__(self, service: str, heartbeat_interval: float = 60.0):
        self.service = service
        self.heartbeat_interval = heartbeat_interval
        self._repo: Optional[ServiceAuditRepository] = None
        self._last_heartbeat_at: float = 0.0

    async def _get_repo(self) -> ServiceAuditRepository:
        if self._repo is None:
            from xyz_agent_context.utils import get_db_client
            self._repo = ServiceAuditRepository(await get_db_client())
        return self._repo

    async def _emit(self, event_type: str, detail: Any = None) -> None:
        try:
            repo = await self._get_repo()
            await repo.record(self.service, event_type, detail)
        except Exception as e:  # noqa: BLE001 — observer never breaks observed
            logger.warning(f"[ServiceAudit] {self.service}/{event_type} failed: {e}")

    async def started(self, detail: Any = None) -> None:
        await self._emit(EVENT_STARTED, detail)

    async def stopped(self, detail: Any = None) -> None:
        await self._emit(EVENT_STOPPED, detail)

    async def error(self, detail: Any = None) -> None:
        await self._emit(EVENT_ERROR, detail)

    async def heartbeat(self, detail: Any = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_heartbeat_at) < self.heartbeat_interval:
            return
        self._last_heartbeat_at = now
        await self._emit(EVENT_HEARTBEAT, detail)
