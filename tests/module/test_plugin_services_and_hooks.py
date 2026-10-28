"""
@file_name: test_plugin_services_and_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: Builtins reach the platform only through services and host hooks: skills workspace / job services on the locator, job re-arm, identity-record and Manyfold credential-export hooks — and nothing listens when the builtin is disabled.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.contracts.job import JobRunOutcome
from narranexus.kernel.plugins.registries import Registries
from narranexus.contracts.services import JOB_INSTANCES, JOB_RUN_ONCE, SKILL_WORKSPACES
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.utils import plugin_services
from narranexus.platform.utils.host_hooks import call_host_hook

CHANNEL_OWNERS = {f"builtin.channels.{c}" for c in ("telegram", "discord", "slack", "wechat", "lark", "narramessenger")}


def _regs() -> Registries:
    regs = Registries()
    load_builtins(regs, "backend")
    load_builtins(regs, "backend")
    return regs


def test_builtins_expose_their_services_and_release_them_with_the_owner():
    regs = _regs()
    assert regs.services.ids() == (
        "jobs.instances", "jobs.resume_for_principal", "jobs.run_once", "skills.workspaces",
    )
    assert regs.services.owner_of(SKILL_WORKSPACES) == "builtin.skills"
    regs.remove_owner("builtin.skills")
    assert regs.services.try_require(SKILL_WORKSPACES) is None
    assert regs.services.try_require(JOB_INSTANCES) is not None


def test_platform_accessors_resolve_the_builtin_implementations():
    from narranexus_plugins.skill_module import SkillModule

    assert isinstance(plugin_services.skill_workspace("agent_x", "user_y"), SkillModule)
    assert hasattr(plugin_services.job_instances(object()), "create_job_with_instance")
    assert callable(plugin_services.try_job_run_once())
    assert callable(plugin_services.try_job_resume_for_principal())


@pytest.mark.asyncio
async def test_run_once_service_delegates_to_the_job_module(monkeypatch):
    from narranexus_plugins.job_module import run_once as ro

    monkeypatch.setattr(ro, "run_job_once", AsyncMock(return_value=JobRunOutcome(job_id="j1", ok=True, status="completed")))
    outcome = await plugin_services.try_job_run_once()("a1", "j1")
    assert outcome == JobRunOutcome(job_id="j1", ok=True, status="completed")


@pytest.mark.asyncio
async def test_manyfold_run_job_degrades_without_builtin_job(monkeypatch):
    from backend.routes.manyfold import sync

    monkeypatch.setattr(sync, "try_job_run_once", lambda: None)
    outcome = await sync.execute_job_once("a1", "j1")
    assert outcome.ok is False and outcome.reason == "jobs_unavailable"

    async def runner(agent_id, job_id):
        return JobRunOutcome(job_id=job_id, ok=True, status="completed", drained=2)

    monkeypatch.setattr(sync, "try_job_run_once", lambda: runner)
    outcome = await sync.execute_job_once("a1", "j1")
    assert (outcome.ok, outcome.status, outcome.drained) == (True, "completed", 2) and hasattr(outcome, "as_text")


@pytest.mark.asyncio
async def test_user_runnability_event_rearms_jobs_through_the_hook(monkeypatch):
    from narranexus_plugins.job_module import job_recovery

    seen: list[str] = []
    monkeypatch.setattr(job_recovery, "schedule_user_no_quota_rearm", seen.append)
    from backend.host_events import notify_user_runnability_changed

    await notify_user_runnability_changed("u1")
    assert seen == ["u1"]


@pytest.mark.asyncio
async def test_identity_record_events_reach_awareness_with_the_callers_db(monkeypatch):
    from narranexus.platform.agent_profile._agent_profile_impl import profile_write
    from narranexus_plugins import awareness_module as aw

    record = AsyncMock(return_value=True)
    reconcile = AsyncMock(return_value=False)
    monkeypatch.setattr(aw, "record_identity_change", record)
    monkeypatch.setattr(aw, "reconcile_identity_record", reconcile)
    db = object()
    assert await profile_write._record_identity(db, "a1", "Old", "New") is True
    assert record.await_args.args == (db, "a1", "Old", "New")
    assert await profile_write._reconcile_identity(db, "a1", "New") is False
    assert reconcile.await_args.args == (db, "a1", "New")


@pytest.mark.asyncio
async def test_identity_events_are_silent_when_awareness_is_disabled(monkeypatch):
    from narranexus.platform.agent_profile._agent_profile_impl import profile_write

    regs = _regs()
    regs.remove_owner("builtin.awareness")
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)
    assert await profile_write._record_identity(object(), "a1", "Old", "New") is None
    assert await profile_write._reconcile_identity(object(), "a1", "New") is None


@pytest.mark.asyncio
async def test_listener_exceptions_propagate_to_the_rename_transaction(monkeypatch):
    from narranexus.platform.agent_profile._agent_profile_impl import profile_write
    from narranexus_plugins import awareness_module as aw

    async def boom(*a):
        raise RuntimeError("identity write failed")

    monkeypatch.setattr(aw, "record_identity_change", boom)
    with pytest.raises(RuntimeError, match="identity write failed"):
        await profile_write._record_identity(object(), "a1", "Old", "New")


@pytest.mark.asyncio
async def test_every_channel_answers_the_credential_export_hook(monkeypatch):
    from narranexus_plugins.telegram_module import _telegram_credential_manager as tg

    class FakeManager:
        def __init__(self, db):
            pass

        async def list_active(self):
            return [SimpleNamespace(agent_id="a1", enabled=1, bot_user_id="42", bot_token="tok", bot_username="bot")]

    monkeypatch.setattr(tg, "TelegramCredentialManager", FakeManager)
    regs = _regs()
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)
    assert set(regs.hooks.caller("onWillExportManagedChannels").owners()) == CHANNEL_OWNERS
    outcome = await call_host_hook("onWillExportManagedChannels", db=object())
    rows = [row for rows in outcome.results for row in rows]
    assert rows == [
        {
            "provider": "telegram",
            "agent_id": "a1",
            "enabled": True,
            "external_id": "42",
            "credentials": {"bot_token": "tok"},
            "config": {"bot_username": "bot", "bot_user_id": "42"},
        }
    ]
    # The other five hit a fake db and fail in isolation — errors, not exceptions.
    assert {owner for owner, _ in outcome.errors} == CHANNEL_OWNERS - {"builtin.channels.telegram"}


def test_platform_sources_import_no_builtin():
    from backend.routes.manyfold import sync
    from narranexus.platform.agent_profile._agent_profile_impl import profile_write
    from narranexus.platform.bundle import importer

    for mod in (sync, profile_write, importer):
        src = inspect.getsource(mod)
        assert "narranexus_plugins." + "awareness_module" not in src
        assert "_credential_manager" not in src
        assert "skill_module" + ".skill_module" not in src
