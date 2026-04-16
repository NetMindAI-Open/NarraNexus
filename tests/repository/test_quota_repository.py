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
async def test_atomic_deduct_concurrent_does_not_lose_updates(repo):
    await repo.create("usr_dave", 10_000, 10_000)
    tasks = [repo.atomic_deduct("usr_dave", 100, 10) for _ in range(50)]
    await asyncio.gather(*tasks)
    q = await repo.get_by_user_id("usr_dave")
    assert q.used_input_tokens == 5000
    assert q.used_output_tokens == 500


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
