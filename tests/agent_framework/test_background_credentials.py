"""
@file_name: test_background_credentials.py
@date: 2026-07-07
@description: Tests for inject_owner_helper_credentials — the shared entry
point every DETACHED background task uses to run against the agent owner's
Helper LLM instead of the platform key.
"""

import pytest

from xyz_agent_context.agent_framework import provider_resolver


class _FakeDB:
    def __init__(self, agent_row):
        self._agent_row = agent_row
        self.queries = []

    async def get_one(self, table, filters):
        self.queries.append((table, filters))
        return self._agent_row


@pytest.mark.asyncio
async def test_injects_owner_config_when_owner_present(monkeypatch):
    cleared = {"n": 0}
    resolved = {"user": None}

    monkeypatch.setattr(
        provider_resolver, "clear_user_config",
        lambda: cleared.__setitem__("n", cleared["n"] + 1),
    )

    async def _fake_resolve(user_id, db, agent_id=None):
        resolved["user"] = user_id

    monkeypatch.setattr(
        provider_resolver, "resolve_and_set_provider_for_user", _fake_resolve
    )

    db = _FakeDB({"agent_id": "agt_1", "created_by": "usr_owner"})
    owner = await provider_resolver.inject_owner_helper_credentials("agt_1", db)

    assert owner == "usr_owner"
    assert resolved["user"] == "usr_owner"
    # ContextVars reset first so a prior tenant's creds can't leak.
    assert cleared["n"] == 1
    assert db.queries == [("agents", {"agent_id": "agt_1"})]


@pytest.mark.asyncio
async def test_returns_none_and_does_not_resolve_when_no_owner(monkeypatch):
    called = {"resolve": False}

    monkeypatch.setattr(provider_resolver, "clear_user_config", lambda: None)

    async def _fake_resolve(user_id, db, agent_id=None):
        called["resolve"] = True

    monkeypatch.setattr(
        provider_resolver, "resolve_and_set_provider_for_user", _fake_resolve
    )

    db = _FakeDB({"agent_id": "agt_1", "created_by": ""})
    owner = await provider_resolver.inject_owner_helper_credentials("agt_1", db)

    assert owner is None
    assert called["resolve"] is False


@pytest.mark.asyncio
async def test_returns_none_when_agent_row_missing(monkeypatch):
    monkeypatch.setattr(provider_resolver, "clear_user_config", lambda: None)

    async def _fake_resolve(user_id, db, agent_id=None):  # pragma: no cover - must not run
        raise AssertionError("should not resolve without an owner")

    monkeypatch.setattr(
        provider_resolver, "resolve_and_set_provider_for_user", _fake_resolve
    )

    db = _FakeDB(None)
    owner = await provider_resolver.inject_owner_helper_credentials("ghost", db)
    assert owner is None


@pytest.mark.asyncio
async def test_resolver_errors_propagate(monkeypatch):
    monkeypatch.setattr(provider_resolver, "clear_user_config", lambda: None)

    async def _boom(user_id, db, agent_id=None):
        raise provider_resolver.QuotaExceededError("free tier exhausted")

    monkeypatch.setattr(
        provider_resolver, "resolve_and_set_provider_for_user", _boom
    )

    db = _FakeDB({"agent_id": "agt_1", "created_by": "usr_owner"})
    with pytest.raises(provider_resolver.ProviderResolverError):
        await provider_resolver.inject_owner_helper_credentials("agt_1", db)
