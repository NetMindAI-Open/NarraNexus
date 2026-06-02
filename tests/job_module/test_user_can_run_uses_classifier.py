"""
@file_name: test_user_can_run_uses_classifier.py
@author: Bin Liang
@date: 2026-06-01
@description: Regression for the 2026-05-31 pause/resume oscillation.

`JobTrigger._user_can_run` (the gate that lifts PAUSED_NO_QUOTA back to ACTIVE)
must delegate to the single classifier `ProviderResolver.classify` and accept a
run iff `is_runnable(verdict)`. The bug was that the old `_user_can_run`
reimplemented the tree as "quota OR own-provider-complete", ignoring
`prefer_system_override` — so a user who opted in to the (exhausted) free tier
but also had an own provider was judged "can run", resumed, then rejected by the
runtime (which won't fall back to the own key), forever.
"""
import pytest

from xyz_agent_context.agent_framework.provider_resolver import ProviderAvailability
from xyz_agent_context.module.job_module.job_trigger import JobTrigger

_PATH = "xyz_agent_context.agent_framework.provider_resolver.classify_provider_for_user"


@pytest.mark.asyncio
async def test_free_tier_exhausted_is_not_runnable(db_client, monkeypatch):
    """The elricwan case: pref_system + exhausted + own provider → must NOT run."""
    async def _fake(uid, db):
        return ProviderAvailability.FREE_TIER_EXHAUSTED
    monkeypatch.setattr(_PATH, _fake)
    trigger = JobTrigger(database_client=db_client)
    assert await trigger._user_can_run("elricwan") is False


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
