"""
@file_name: test_cost_tracker_deduct_hook.py
@author: Bin Liang
@date: 2026-04-16
@description: record_cost calls QuotaService.default().deduct only when
provider_source ContextVar is "system". All other cases (user / None /
missing current_user_id / QuotaService not initialized) must be silent
no-ops that do NOT affect the cost_records insert.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.api_config import (
    set_current_user_id,
    set_provider_source,
)
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.utils.cost_tracker import record_cost


@pytest.fixture(autouse=True)
def _reset_ctx():
    set_provider_source(None)
    set_current_user_id(None)
    QuotaService._default = None
    yield
    set_provider_source(None)
    set_current_user_id(None)
    QuotaService._default = None


def _mk_mock_db():
    m = MagicMock()
    m.insert = AsyncMock(return_value=1)
    return m


@pytest.mark.asyncio
async def test_no_deduct_when_source_is_user():
    set_provider_source("user")
    set_current_user_id("usr_x")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_source_is_none():
    set_provider_source(None)
    set_current_user_id("usr_x")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_user_id_missing():
    """System tag without a user id — safe fallback: don't deduct."""
    set_provider_source("system")
    set_current_user_id(None)
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    deduct.assert_not_called()


@pytest.mark.asyncio
async def test_no_deduct_when_quota_service_not_initialized():
    """set_default never called -> hook must silently skip, not raise."""
    set_provider_source("system")
    set_current_user_id("usr_x")

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=20,
    )
    # If this raises, the test fails — we assert by reaching here.


@pytest.mark.asyncio
async def test_deduct_called_with_exact_tokens_when_source_is_system():
    set_provider_source("system")
    set_current_user_id("usr_y")
    deduct = AsyncMock()
    fake_svc = MagicMock()
    fake_svc.deduct = deduct
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=77,
        output_tokens=11,
    )
    # cost_record_id comes from the mock insert (returns 1); the ledger
    # metadata (provider_source / model / agent_id) is threaded through so
    # atomic_deduct can write a self-auditing quota_deductions row.
    deduct.assert_awaited_once_with(
        "usr_y", 77, 11,
        cost_record_id=1,
        provider_source="system",
        model="claude-sonnet-4-5",
        agent_id="a_1",
    )


@pytest.mark.asyncio
async def test_deduct_exceptions_swallowed():
    """Deduct failures must not propagate — cost_tracker is observability, not control."""
    set_provider_source("system")
    set_current_user_id("usr_z")

    async def boom(*a, **k):
        raise RuntimeError("db down")
    fake_svc = MagicMock()
    fake_svc.deduct = boom
    QuotaService.set_default(fake_svc)

    await record_cost(
        db=_mk_mock_db(),
        agent_id="a_1",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=1,
        output_tokens=1,
    )  # must not raise


# --- End-to-end against a real SQLite db: cost_records columns + ledger row ---

from xyz_agent_context.repository.quota_repository import QuotaRepository  # noqa: E402


class _EnabledProvider:
    """Minimal SystemProviderService stand-in: feature enabled."""

    def is_enabled(self) -> bool:
        return True

    def get_initial_quota(self):
        return (10_000, 10_000)


@pytest.mark.asyncio
async def test_record_cost_persists_user_and_writes_ledger(db_client):
    """System branch, real DB: cost_records carries user_id/provider_source and
    a linked quota_deductions ledger row is written in the same flow."""
    repo = QuotaRepository(db_client)
    await repo.create("usr_int", 10_000, 10_000)
    QuotaService.set_default(
        QuotaService(repo=repo, system_provider=_EnabledProvider())
    )
    set_provider_source("system")
    set_current_user_id("usr_int")

    await record_cost(
        db=db_client,
        agent_id="a_int",
        event_id="evt_int",
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=50,
        output_tokens=8,
    )

    cost_rows = await db_client.execute(
        "SELECT id, user_id, provider_source FROM cost_records WHERE agent_id = %s",
        params=("a_int",),
        fetch=True,
    )
    assert len(cost_rows) == 1
    assert cost_rows[0]["user_id"] == "usr_int"
    assert cost_rows[0]["provider_source"] == "system"

    led_rows = await db_client.execute(
        "SELECT user_id, input_tokens, output_tokens, cost_record_id, "
        "provider_source, model, agent_id FROM quota_deductions WHERE user_id = %s",
        params=("usr_int",),
        fetch=True,
    )
    assert len(led_rows) == 1
    led = led_rows[0]
    assert led["input_tokens"] == 50
    assert led["output_tokens"] == 8
    assert led["cost_record_id"] == cost_rows[0]["id"]
    assert led["provider_source"] == "system"
    assert led["model"] == "claude-sonnet-4-5"
    assert led["agent_id"] == "a_int"

    # Running total moved too.
    q = await repo.get_by_user_id("usr_int")
    assert q.used_input_tokens == 50
    assert q.used_output_tokens == 8


@pytest.mark.asyncio
async def test_record_cost_non_system_records_attribution_but_no_ledger(db_client):
    """Non-system call: cost_records is still written WITH attribution
    (user_id + provider_source are captured for every call, not just system),
    but NO quota_deductions ledger row is created (only the system branch
    deducts)."""
    repo = QuotaRepository(db_client)
    QuotaService.set_default(
        QuotaService(repo=repo, system_provider=_EnabledProvider())
    )
    set_provider_source("user")
    set_current_user_id("usr_own")

    await record_cost(
        db=db_client,
        agent_id="a_user",
        event_id=None,
        call_type="agent_loop",
        model="claude-sonnet-4-5",
        input_tokens=30,
        output_tokens=5,
    )

    cost_rows = await db_client.execute(
        "SELECT user_id, provider_source FROM cost_records WHERE agent_id = %s",
        params=("a_user",),
        fetch=True,
    )
    assert len(cost_rows) == 1
    # provider_source is captured verbatim; user_id captured too (attribution is
    # not gated on the system branch — only the DEDUCTION is).
    assert cost_rows[0]["provider_source"] == "user"

    led_rows = await db_client.execute(
        "SELECT id FROM quota_deductions WHERE user_id = %s",
        params=("usr_own",),
        fetch=True,
    )
    assert led_rows == [] or len(led_rows) == 0
