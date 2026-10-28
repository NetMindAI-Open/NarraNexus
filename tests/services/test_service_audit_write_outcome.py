"""
@file_name: test_service_audit_write_outcome.py
@author:
@date: 2026-08-26
@description: Pin that a failed audit INSERT is reported as a failure.

The DM fallback gate arms a 10-minute audit cooldown on the strength of a
successful write. That guarantee is only as good as the weakest link in
`ServiceAuditor.event() -> ServiceAuditRepository.record() -> db.insert()`,
and every link here is written to swallow its own exceptions. A link that
swallows the exception AND the outcome turns "row written" into something
indistinguishable from "the DB was down" — which is how the cooldown ended
up being armed on failures.

The same PR has already shipped one guarantee that only a test fake had.
These tests drive the real repository against a failing db handle.
"""

import pytest

from narranexus.platform.repository.service_audit_repository import (
    ServiceAuditRepository,
)
from narranexus.platform.services.service_audit import ServiceAuditor

pytestmark = pytest.mark.asyncio


class _ExplodingDb:
    async def insert(self, *_a, **_k):
        raise RuntimeError("table is gone")


class _AcceptingDb:
    def __init__(self):
        self.rows = []

    async def insert(self, table, row):
        self.rows.append((table, row))


async def test_a_failed_insert_is_reported_as_a_failed_write():
    repo = ServiceAuditRepository(_ExplodingDb())
    assert await repo.record("svc", "evt", {"a": 1}) is False


async def test_a_landed_insert_is_reported_as_a_landed_write():
    db = _AcceptingDb()
    repo = ServiceAuditRepository(db)
    assert await repo.record("svc", "evt", {"a": 1}) is True
    assert len(db.rows) == 1


async def test_the_auditor_passes_the_repository_outcome_through(monkeypatch):
    """`event()` must not report success merely because nothing was raised.

    The repository catches its own insert errors, so "no exception reached
    me" is true on every failed write. Only the repository's return value
    distinguishes the two.
    """
    auditor = ServiceAuditor("svc")

    async def _repo():
        return ServiceAuditRepository(_ExplodingDb())

    monkeypatch.setattr(auditor, "_get_repo", _repo)
    assert await auditor.event("evt", {"a": 1}) is False

    async def _ok_repo():
        return ServiceAuditRepository(_AcceptingDb())

    monkeypatch.setattr(auditor, "_get_repo", _ok_repo)
    assert await auditor.event("evt", {"a": 1}) is True


async def test_the_auditor_still_never_raises_into_the_observed(monkeypatch):
    """The outcome is reported, not thrown. An observer must not break the
    observed — every caller on this path relies on that."""
    auditor = ServiceAuditor("svc")

    async def _boom():
        raise RuntimeError("no db at all")

    monkeypatch.setattr(auditor, "_get_repo", _boom)
    assert await auditor.event("evt") is False
