"""
@file_name: test_user_can_run_uses_classifier.py
@author: Bin Liang
@date: 2026-06-01
@description: Regression for the 2026-05-31 pause/resume oscillation.

`JobTrigger._user_can_run` (the gate that lifts PAUSED_NO_QUOTA back to ACTIVE)
must delegate to the single classifier `ProviderResolver.classify` and accept a
run iff `is_runnable(verdict)`. The bug was that the old `_user_can_run`
reimplemented the tree and disagreed with the runtime — resume/reject forever.

2026-07-18: the original oscillation case (exhausted free tier + own
provider → FREE_TIER_EXHAUSTED, not runnable) no longer exists — that
verdict was deleted; the same situation now classifies USER_OK and IS
runnable (the own key takes over). The regression fence therefore pins the
delegation itself: every verdict the classifier can return maps through
is_runnable, and a classifier error stays conservatively False.
"""
import pytest

from xyz_agent_context.agent_framework.provider_resolver import ProviderAvailability
from xyz_agent_context.module.job_module.job_trigger import JobTrigger

_PATH = "xyz_agent_context.agent_framework.provider_resolver.classify_provider_for_user"


@pytest.mark.asyncio
async def test_exhausted_with_own_provider_is_runnable_now(db_client, monkeypatch):
    """The 2026-05-31 elricwan situation (exhausted free tier + own provider)
    classifies USER_OK since 2026-07-18 — the own key takes over, so the job
    MUST resume instead of oscillating. (The old FREE_TIER_EXHAUSTED verdict
    that pinned this as not-runnable was deleted with the preference.)"""
    async def _fake(uid, db):
        return ProviderAvailability.USER_OK
    monkeypatch.setattr(_PATH, _fake)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("elricwan") is True


@pytest.mark.asyncio
async def test_quota_exceeded_is_not_runnable(db_client, monkeypatch):
    async def _fake(uid, db):
        return ProviderAvailability.QUOTA_EXCEEDED
    monkeypatch.setattr(_PATH, _fake)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u") is False


@pytest.mark.asyncio
async def test_user_ok_is_runnable(db_client, monkeypatch):
    async def _fake(uid, db):
        return ProviderAvailability.USER_OK
    monkeypatch.setattr(_PATH, _fake)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u") is True


@pytest.mark.asyncio
async def test_system_ok_is_runnable(db_client, monkeypatch):
    async def _fake(uid, db):
        return ProviderAvailability.SYSTEM_OK
    monkeypatch.setattr(_PATH, _fake)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u") is True


@pytest.mark.asyncio
async def test_classifier_error_is_conservative_false(db_client, monkeypatch):
    """If the quota/provider subsystem errors, default to not-runnable (don't
    resume into an unknown state)."""
    async def _boom(uid, db):
        raise RuntimeError("quota subsystem unbootstrapped")
    monkeypatch.setattr(_PATH, _boom)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("u") is False
