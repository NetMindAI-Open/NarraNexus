"""
@file_name: test_quota_service.py
@author: Bin Liang
@date: 2026-04-16
@description: QuotaService orchestration tests — init/check/deduct/grant
with gating by SystemProviderService.is_enabled().
"""
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from xyz_agent_context.schema.quota_schema import QuotaStatus
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.repository.quota_repository import QuotaRepository


def _mk_sys_provider(enabled: bool, initial=(500_000, 100_000)):
    m = MagicMock()
    m.is_enabled.return_value = enabled
    m.get_initial_quota.return_value = initial
    return m


@pytest_asyncio.fixture
async def service(db_client):
    repo = QuotaRepository(db_client)
    return QuotaService(repo=repo, system_provider=_mk_sys_provider(True))


@pytest.mark.asyncio
async def test_init_for_user_when_disabled_is_noop(db_client):
    repo = QuotaRepository(db_client)
    svc = QuotaService(repo=repo, system_provider=_mk_sys_provider(False))
    result = await svc.init_for_user("usr_x")
    assert result is None
    assert await repo.get_by_user_id("usr_x") is None


@pytest.mark.asyncio
async def test_init_for_user_creates_row_and_is_idempotent(service):
    first = await service.init_for_user("usr_a")
    assert first is not None
    assert first.initial_input_tokens == 500_000
    second = await service.init_for_user("usr_a")
    assert second is not None
    assert second.user_id == "usr_a"
    assert second.initial_input_tokens == 500_000


@pytest.mark.asyncio
async def test_check_returns_true_when_budget_available(service):
    await service.init_for_user("usr_b")
    assert await service.check("usr_b") is True


@pytest.mark.asyncio
async def test_check_returns_false_when_no_record(service):
    assert await service.check("usr_nobody") is False


@pytest.mark.asyncio
async def test_check_returns_false_when_feature_disabled(db_client):
    repo = QuotaRepository(db_client)
    await repo.create("usr_c", 1000, 200)
    svc = QuotaService(repo=repo, system_provider=_mk_sys_provider(False))
    assert await svc.check("usr_c") is False


@pytest.mark.asyncio
async def test_deduct_when_disabled_is_noop(db_client):
    repo = QuotaRepository(db_client)
    await repo.create("usr_d", 1000, 200)
    svc = QuotaService(repo=repo, system_provider=_mk_sys_provider(False))
    await svc.deduct("usr_d", 100, 20)
    q = await repo.get_by_user_id("usr_d")
    assert q.used_input_tokens == 0


@pytest.mark.asyncio
async def test_deduct_applies_when_enabled(service):
    await service.init_for_user("usr_e")
    await service.deduct("usr_e", 100, 20)
    q = await service.get("usr_e")
    assert q.used_input_tokens == 100
    assert q.used_output_tokens == 20


@pytest.mark.asyncio
async def test_deduct_swallows_exceptions(service, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(service.repo, "atomic_deduct", boom)
    await service.deduct("usr_whatever", 1, 1)  # must not raise


@pytest.mark.asyncio
async def test_grant_upserts_when_no_record(service):
    result = await service.grant("usr_new", 500, 100)
    assert result.granted_input_tokens == 500
    assert result.initial_input_tokens == 0
    assert result.status == QuotaStatus.ACTIVE


@pytest.mark.asyncio
async def test_grant_existing_record_adds(service):
    await service.init_for_user("usr_f")
    result = await service.grant("usr_f", 200_000, 40_000)
    assert result.granted_input_tokens == 200_000
    assert result.initial_input_tokens == 500_000


@pytest.mark.asyncio
async def test_default_classmethod(service):
    QuotaService.set_default(service)
    assert QuotaService.default() is service
    QuotaService._default = None
    with pytest.raises(RuntimeError):
        QuotaService.default()
