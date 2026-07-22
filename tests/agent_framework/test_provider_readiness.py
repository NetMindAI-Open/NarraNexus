"""
@file_name: test_provider_readiness.py
@author: Bin Liang
@date: 2026-06-01
@description: ProviderReadiness.validate — the live readiness check used by
edge-triggered PAUSED_NO_QUOTA recovery. Two tiers: cheap static verdict first
(short-circuit if not runnable), then a live provider test for USER_OK only.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.provider_resolver import ProviderAvailability
from xyz_agent_context.agent_framework import provider_readiness as pr
from xyz_agent_context.agent_framework.provider_readiness import ProviderReadiness

_CLASSIFY = "xyz_agent_context.agent_framework.provider_readiness.classify_provider_for_user"
_UPS = "xyz_agent_context.agent_framework.user_provider_service.UserProviderService"


def _patch_classify(monkeypatch, verdict=None, raises=False):
    async def _f(uid, db):
        if raises:
            raise RuntimeError("boom")
        return verdict
    monkeypatch.setattr(_CLASSIFY, _f)


def _patch_ups(monkeypatch, *, test_result=None, test_raises=False, has_agent_slot=True):
    inst = MagicMock()
    slot = MagicMock(); slot.provider_id = "prov_x"
    cfg = MagicMock(); cfg.slots = {"agent": slot} if has_agent_slot else {}
    inst.get_user_config = AsyncMock(return_value=cfg)
    if test_raises:
        inst.test_provider = AsyncMock(side_effect=RuntimeError("net down"))
    else:
        inst.test_provider = AsyncMock(return_value=test_result)
    monkeypatch.setattr(_UPS, lambda db: inst)
    return inst


@pytest.mark.asyncio
async def test_classify_error_is_not_ready(monkeypatch):
    _patch_classify(monkeypatch, raises=True)
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is False and reason == "classify_error"


@pytest.mark.asyncio
async def test_not_runnable_verdict_short_circuits(monkeypatch):
    _patch_classify(monkeypatch, verdict=ProviderAvailability.QUOTA_EXCEEDED)
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is False and reason == "quota_exceeded"


@pytest.mark.asyncio
async def test_system_ok_skips_live_test(monkeypatch):
    _patch_classify(monkeypatch, verdict=ProviderAvailability.SYSTEM_OK)
    # No UPS patch — if it tried to live-test it would blow up importing the real one.
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is True and reason == "system_ok"


@pytest.mark.asyncio
async def test_user_ok_live_test_passes(monkeypatch):
    _patch_classify(monkeypatch, verdict=ProviderAvailability.USER_OK)
    _patch_ups(monkeypatch, test_result=(True, "ok"))
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is True and reason == "ok"


@pytest.mark.asyncio
async def test_user_ok_live_test_fails(monkeypatch):
    _patch_classify(monkeypatch, verdict=ProviderAvailability.USER_OK)
    _patch_ups(monkeypatch, test_result=(False, "401 unauthorized"))
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is False and reason == "401 unauthorized"


@pytest.mark.asyncio
async def test_user_ok_live_test_error_does_not_block_recovery(monkeypatch):
    """A flaky live test must not strand a user whose config is otherwise valid."""
    _patch_classify(monkeypatch, verdict=ProviderAvailability.USER_OK)
    _patch_ups(monkeypatch, test_raises=True)
    ok, reason = await ProviderReadiness.validate("u", db=None)
    assert ok is True and reason == "user_ok"
