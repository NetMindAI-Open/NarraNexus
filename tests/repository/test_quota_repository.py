"""
@file_name: test_quota_repository.py
@author: Bin Liang
@date: 2026-04-16
@description: QuotaRepository CRUD + atomic concurrency tests.
"""
import asyncio
import pytest
import pytest_asyncio

from xyz_agent_context.schema.quota_schema import Quota, QuotaStatus
from xyz_agent_context.repository.quota_repository import QuotaRepository


@pytest_asyncio.fixture
async def repo(db_client):
    return QuotaRepository(db_client)


@pytest.mark.asyncio
async def test_create_and_get_by_user_id(repo):
    created = await repo.create(
        user_id="usr_alice",
        initial_input_tokens=1000,
        initial_output_tokens=200,
    )
    assert created.user_id == "usr_alice"
    assert created.used_input_tokens == 0

    fetched = await repo.get_by_user_id("usr_alice")
    assert fetched is not None
    assert fetched.initial_input_tokens == 1000


@pytest.mark.asyncio
async def test_get_by_user_id_returns_none_when_absent(repo):
    assert await repo.get_by_user_id("usr_ghost") is None


@pytest.mark.asyncio
async def test_atomic_deduct_single(repo):
    await repo.create("usr_bob", 1000, 200)
    await repo.atomic_deduct("usr_bob", 100, 20)
    q = await repo.get_by_user_id("usr_bob")
    assert q.used_input_tokens == 100
    assert q.used_output_tokens == 20
    assert q.status == QuotaStatus.ACTIVE


@pytest.mark.asyncio
async def test_atomic_deduct_flips_status_to_exhausted_when_overdrawn(repo):
    await repo.create("usr_carol", 100, 20)
    await repo.atomic_deduct("usr_carol", 100, 20)  # exactly consumes
    q = await repo.get_by_user_id("usr_carol")
    assert q.status == QuotaStatus.EXHAUSTED


@pytest.mark.asyncio
async def test_atomic_deduct_concurrent_does_not_lose_updates(repo, db_client):
    await repo.create("usr_dave", 10_000, 10_000)
    tasks = [repo.atomic_deduct("usr_dave", 100, 10) for _ in range(50)]
    await asyncio.gather(*tasks)
    q = await repo.get_by_user_id("usr_dave")
    assert q.used_input_tokens == 5000
    assert q.used_output_tokens == 500
    # Ledger must stay consistent under concurrency: exactly one row per deduct,
    # summing to the running total. This is the regression guard against the
    # (rejected) client-transaction approach, which collided under concurrency.
    rows = await db_client.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(input_tokens), 0) AS si "
        "FROM quota_deductions WHERE user_id = %s",
        params=("usr_dave",),
        fetch=True,
    )
    assert rows[0]["n"] == 50
    assert rows[0]["si"] == 5000


@pytest.mark.asyncio
async def test_atomic_deduct_writes_ledger_row(repo, db_client):
    await repo.create("usr_led", 1000, 200)
    await repo.atomic_deduct(
        "usr_led", 100, 20,
        cost_record_id=42,
        provider_source="system",
        model="claude-x",
        agent_id="a_led",
    )
    rows = await db_client.execute(
        "SELECT input_tokens, output_tokens, cost_record_id, provider_source, "
        "model, agent_id FROM quota_deductions WHERE user_id = %s",
        params=("usr_led",),
        fetch=True,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["input_tokens"] == 100
    assert r["output_tokens"] == 20
    assert r["cost_record_id"] == 42
    assert r["provider_source"] == "system"
    assert r["model"] == "claude-x"
    assert r["agent_id"] == "a_led"


@pytest.mark.asyncio
async def test_atomic_deduct_ledger_failure_does_not_skip_charge(
    repo, db_client, monkeypatch
):
    """UPDATE-first ordering: a ledger-write failure must NOT skip the charge
    (that would be silent free-tier consumption — the 2026-04-22 incident
    shape). The counter still moves, no exception propagates, and a durable
    service_audit row records the missing-ledger event."""
    await repo.create("usr_rb", 1000, 200)

    orig_insert = db_client.insert

    async def boom(table, data):
        if table == "quota_deductions":
            raise RuntimeError("ledger insert boom")
        return await orig_insert(table, data)

    monkeypatch.setattr(db_client, "insert", boom)

    # Must NOT raise — the charge is the primary invariant; audit is best-effort.
    await repo.atomic_deduct("usr_rb", 100, 20, cost_record_id=1)

    # Counter moved (charge applied).
    q = await repo.get_by_user_id("usr_rb")
    assert q.used_input_tokens == 100
    assert q.used_output_tokens == 20
    # No ledger row (its insert failed) ...
    led = await db_client.execute(
        "SELECT id FROM quota_deductions WHERE user_id = %s",
        params=("usr_rb",),
        fetch=True,
    )
    assert len(led) == 0
    # ... but a durable audit trail of the failure exists (written via
    # ServiceAuditRepository.record: EVENT_ERROR + JSON detail carrying the
    # subtype under `reason`, so the System page can actually parse it).
    audit = await db_client.execute(
        "SELECT detail FROM service_audit WHERE service = %s AND event_type = %s",
        params=("quota", "error"),
        fetch=True,
    )
    assert len(audit) == 1
    assert "ledger_write_failed" in audit[0]["detail"]
    assert "usr_rb" in audit[0]["detail"]


@pytest.mark.asyncio
async def test_atomic_grant_adds_and_reactivates(repo):
    await repo.create("usr_eve", 100, 20)
    await repo.atomic_deduct("usr_eve", 100, 20)  # exhausted
    q = await repo.get_by_user_id("usr_eve")
    assert q.status == QuotaStatus.EXHAUSTED

    await repo.atomic_grant("usr_eve", 500, 100)
    q = await repo.get_by_user_id("usr_eve")
    assert q.granted_input_tokens == 500
    assert q.granted_output_tokens == 100
    assert q.status == QuotaStatus.ACTIVE
