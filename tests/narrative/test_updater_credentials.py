"""
@file_name: test_updater_credentials.py
@date: 2026-07-07
@description: Wiring tests proving the narrative updater's detached background
task resolves the OWNER's Helper LLM before calling the helper SDK, and alerts
(instead of silently swallowing) on credential failures.
"""

import types

import pytest

from xyz_agent_context.agent_framework import provider_resolver
from xyz_agent_context.narrative._narrative_impl import updater as updater_mod
from xyz_agent_context.utils import db_factory
from xyz_agent_context.services import background_llm_alerts as alerts


def _make_updater():
    upd = updater_mod.NarrativeUpdater("agt_1")
    return upd


def _fake_narrative():
    # Minimal stand-in — the updater only reads .id / .agent_id in the paths
    # under test (LLM call + context build are monkeypatched).
    return types.SimpleNamespace(id="nar_1", agent_id="agt_1")


@pytest.fixture(autouse=True)
def _reset():
    alerts.reset_alert_state()
    yield


@pytest.mark.asyncio
async def test_injects_owner_creds_before_llm_call(monkeypatch):
    order = []

    async def _fake_get_db():
        return object()

    async def _fake_inject(agent_id, db):
        order.append(("inject", agent_id))
        return "usr_owner"

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    monkeypatch.setattr(
        provider_resolver, "inject_owner_helper_credentials", _fake_inject
    )

    upd = _make_updater()

    async def _fake_ctx(narrative, event):
        return "ctx"

    async def _fake_call(narrative, context):
        order.append(("llm", narrative.id))
        return None  # nothing to apply — keeps the test focused on ordering

    monkeypatch.setattr(upd, "_build_update_context", _fake_ctx)
    monkeypatch.setattr(upd, "_call_llm_for_update", _fake_call)

    await upd._async_llm_update(_fake_narrative(), object())

    # Credentials resolved BEFORE the helper SDK call.
    assert order == [("inject", "agt_1"), ("llm", "nar_1")]


@pytest.mark.asyncio
async def test_resolver_failure_skips_llm_and_alerts(monkeypatch):
    llm_called = {"n": 0}
    captured = {}

    async def _fake_get_db():
        return object()

    async def _fake_inject(agent_id, db):
        raise provider_resolver.QuotaExceededError("free tier exhausted")

    async def _fake_alert(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    monkeypatch.setattr(
        provider_resolver, "inject_owner_helper_credentials", _fake_inject
    )
    monkeypatch.setattr(alerts, "alert_background_llm_failure", _fake_alert)

    upd = _make_updater()

    async def _fake_call(narrative, context):
        llm_called["n"] += 1
        return None

    monkeypatch.setattr(upd, "_call_llm_for_update", _fake_call)

    await upd._async_llm_update(_fake_narrative(), object())

    assert llm_called["n"] == 0  # never touched the platform key
    assert captured.get("source") == "narrative_update"
    assert captured.get("agent_id") == "agt_1"


@pytest.mark.asyncio
async def test_credential_error_during_call_alerts(monkeypatch):
    captured = {}

    async def _fake_get_db():
        return object()

    async def _fake_inject(agent_id, db):
        return "usr_owner"

    async def _fake_alert(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    monkeypatch.setattr(
        provider_resolver, "inject_owner_helper_credentials", _fake_inject
    )
    monkeypatch.setattr(alerts, "alert_background_llm_failure", _fake_alert)

    upd = _make_updater()

    async def _fake_ctx(narrative, event):
        return "ctx"

    async def _fake_call(narrative, context):
        raise RuntimeError("Incorrect API key provided: sk-proj-...fXQA")

    monkeypatch.setattr(upd, "_build_update_context", _fake_ctx)
    monkeypatch.setattr(upd, "_call_llm_for_update", _fake_call)

    await upd._async_llm_update(_fake_narrative(), object())

    assert captured.get("owner_user_id") == "usr_owner"
    assert captured.get("source") == "narrative_update"
