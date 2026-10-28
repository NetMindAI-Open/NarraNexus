"""
@file_name: test_greeting_refresh.py
@author: Bin Liang
@date: 2026-09-08
@description: Renaming a blank-provisioned agent re-renders its nameless first-run greeting; customised greetings are left alone.

Found on dev (2026-09-08): the creation studio provisions a blank row, names it
a moment later, and the first chat turn still seeded "I don't have a name yet".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.bootstrap import greeting_seed
from narranexus.platform.bootstrap.template import BOOTSTRAP_GREETING


def _repo(monkeypatch, metadata):
    agent = SimpleNamespace(agent_metadata=metadata, created_by="u1")
    repo = SimpleNamespace(get_agent=AsyncMock(return_value=agent), update_agent=AsyncMock(return_value=1))
    monkeypatch.setattr(greeting_seed, "AgentRepository", lambda db: repo)
    return repo


@pytest.mark.asyncio
async def test_nameless_default_greeting_is_rerendered_with_the_new_name(monkeypatch):
    repo = _repo(monkeypatch, {"bootstrap_profile": "default", "bootstrap_greeting": BOOTSTRAP_GREETING, "bootstrap_auto_delete_after_events": 3})
    assert await greeting_seed.refresh_bootstrap_greeting_after_rename(object(), "agent_1", "无心昌") is True
    written = repo.update_agent.await_args.args[1]["agent_metadata"]
    assert "无心昌" in written["bootstrap_greeting"] and "don't have a name" not in written["bootstrap_greeting"]
    assert written["bootstrap_auto_delete_after_events"] == 3  # the rest of the metadata survives


@pytest.mark.asyncio
async def test_a_customised_greeting_is_never_overwritten(monkeypatch):
    repo = _repo(monkeypatch, {"bootstrap_profile": "default", "bootstrap_greeting": "Welcome, commander."})
    assert await greeting_seed.refresh_bootstrap_greeting_after_rename(object(), "agent_1", "无心昌") is False
    repo.update_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_bootstrap_greeting_is_a_noop(monkeypatch):
    repo = _repo(monkeypatch, {})
    assert await greeting_seed.refresh_bootstrap_greeting_after_rename(object(), "agent_1", "无心昌") is False
    repo.update_agent.assert_not_awaited()
