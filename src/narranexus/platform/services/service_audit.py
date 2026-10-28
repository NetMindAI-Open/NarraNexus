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

from narranexus.platform.repository.service_audit_repository import (
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
        # -inf, not 0.0: the gate below is
        # `time.monotonic() - _last_heartbeat_at < heartbeat_interval`, and
        # monotonic() counts from BOOT on Linux — so 0.0 reads as "beat at
        # boot", and on a host younger than the interval the first beat is
        # silently skipped. Same bug shape as the channel trigger's two marks.
        self._last_heartbeat_at: float = float("-inf")

    async def _get_repo(self) -> ServiceAuditRepository:
        if self._repo is None:
            from narranexus.platform.utils import get_db_client
            self._repo = ServiceAuditRepository(await get_db_client())
        return self._repo

    async def _emit(self, event_type: str, detail: Any = None) -> bool:
        """Write one row. Returns whether it landed.

        Still never raises — an observer must not break the observed, and
        every caller here relies on that. But swallowing the exception used
        to also swallow the OUTCOME, so a caller could not tell "written"
        from "the DB was down". A caller that caches a decision on the
        strength of a successful write (see the DM fallback gate's audit
        cooldown) was silently caching it on failures too.
        """
        try:
            repo = await self._get_repo()
            # The repository swallows its own insert errors, so its return
            # value — not the absence of an exception — is what says the row
            # landed. Without it this would only ever report failures to
            # ACQUIRE the db, and a dead insert would still read as written.
            return await repo.record(self.service, event_type, detail)
        except Exception as e:  # noqa: BLE001 — observer never breaks observed
            logger.warning(f"[ServiceAudit] {self.service}/{event_type} failed: {e}")
            return False

    async def event(self, event_type: str, detail: Any = None) -> bool:
        """Emit an arbitrary named audit event.

        The public door onto ``_emit`` for callers that need an event name
        outside the started/stopped/error/heartbeat lifecycle (e.g. a tool
        booking ``inbox_write_failed``). Like the rest, it never raises —
        an observer must not break the observed.

        Returns whether the row landed. Most callers ignore it; a caller
        that caches a decision because it believes the row was written
        ("only audit this conversation once per window") must not cache it
        on a failed write, and before this returned a value it could not
        tell the difference.
        """
        return await self._emit(event_type, detail)

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
