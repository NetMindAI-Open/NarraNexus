"""
@file_name: test_invite_code_repository.py
@author: NarraNexus
@date: 2026-05-14
@description: TDD tests for InviteCodeRepository.

Uses real in-memory SQLite (conftest db_client fixture). The critical
test is consume() atomicity — two callers racing on one code, only one
wins.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.repository.invite_code_repository import (
    InviteCodeRepository,
)


@pytest.fixture
def repo(db_client):
    return InviteCodeRepository(db_client)


@pytest.mark.asyncio
async def test_create_issues_unique_codes(repo):
    a = await repo.create("alice@example.com")
    b = await repo.create("bob@example.com")
    assert a.code != b.code
    assert a.status == "issued"
    assert a.issued_at is not None
    assert a.email == "alice@example.com"


@pytest.mark.asyncio
async def test_create_waitlisted_has_no_issued_at(repo):
    c = await repo.create("late@example.com", status="waitlisted")
    assert c.status == "waitlisted"
    assert c.issued_at is None


@pytest.mark.asyncio
async def test_get_by_code_roundtrip(repo):
    created = await repo.create("x@example.com")
    fetched = await repo.get_by_code(created.code)
    assert fetched is not None
    assert fetched.code == created.code
    assert await repo.get_by_code("NX-NOPENOPE") is None


@pytest.mark.asyncio
async def test_consume_marks_used(repo):
    code = (await repo.create("u@example.com")).code
    assert await repo.consume(code, "user_1") is True
    row = await repo.get_by_code(code)
    assert row.status == "used"
    assert row.used_by_user_id == "user_1"
    assert row.used_at is not None


@pytest.mark.asyncio
async def test_consume_twice_only_first_wins(repo):
    """The race guard: a second consume of the same code must return False."""
    code = (await repo.create("race@example.com")).code
    first = await repo.consume(code, "user_1")
    second = await repo.consume(code, "user_2")
    assert first is True
    assert second is False
    row = await repo.get_by_code(code)
    assert row.used_by_user_id == "user_1"  # second caller did not overwrite


@pytest.mark.asyncio
async def test_consume_unknown_code_returns_false(repo):
    assert await repo.consume("NX-DOESNOTX", "user_1") is False


@pytest.mark.asyncio
async def test_revert_consume_restores_issued(repo):
    code = (await repo.create("rev@example.com")).code
    await repo.consume(code, "user_1")
    await repo.revert_consume(code)
    row = await repo.get_by_code(code)
    assert row.status == "issued"
    assert row.used_by_user_id is None
    assert row.used_at is None
    # ...and it can be consumed again afterwards
    assert await repo.consume(code, "user_2") is True


@pytest.mark.asyncio
async def test_count_active_excludes_waitlisted_and_revoked(repo):
    issued = await repo.create("a@example.com")
    await repo.create("b@example.com")  # second issued
    await repo.create("c@example.com", status="waitlisted")
    await repo.consume(issued.code, "user_1")  # now used
    revoked = await repo.create("d@example.com")
    await repo.revoke(revoked.code)
    # active = issued(1) + used(1); waitlisted + revoked excluded
    assert await repo.count_active() == 2


@pytest.mark.asyncio
async def test_list_for_email_returns_all_rows(repo):
    await repo.create("same@example.com")
    await repo.create("same@example.com", status="waitlisted")
    rows = await repo.list_for_email("same@example.com")
    assert len(rows) == 2
    assert await repo.list_for_email("nobody@example.com") == []


@pytest.mark.asyncio
async def test_promote_waitlisted_to_issued(repo):
    code = (await repo.create("wl@example.com", status="waitlisted")).code
    assert await repo.promote(code) is True
    row = await repo.get_by_code(code)
    assert row.status == "issued"
    assert row.issued_at is not None


@pytest.mark.asyncio
async def test_promote_non_waitlisted_returns_false(repo):
    code = (await repo.create("iss@example.com")).code  # already issued
    assert await repo.promote(code) is False


@pytest.mark.asyncio
async def test_revoke_issued_then_cannot_be_consumed(repo):
    code = (await repo.create("kill@example.com")).code
    assert await repo.revoke(code) is True
    assert await repo.get_by_code(code) is not None
    assert (await repo.get_by_code(code)).status == "revoked"
    assert await repo.consume(code, "user_1") is False


@pytest.mark.asyncio
async def test_revoke_used_code_returns_false(repo):
    code = (await repo.create("done@example.com")).code
    await repo.consume(code, "user_1")
    assert await repo.revoke(code) is False  # used codes can't be revoked
