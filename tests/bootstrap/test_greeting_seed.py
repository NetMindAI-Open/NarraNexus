"""
@file_name: test_greeting_seed.py
@author: Bin Liang
@date: 2026-08-20
@description: Lock resolve_bootstrap_greeting_to_seed — the "should this agent be
seeded" decision. It gates on the SHARED lifecycle.is_bootstrap_active (tested in
tests/bootstrap/test_lifecycle.py), on owner ownership, and on a non-empty
greeting. Removing any of those guards turns one of these red.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.bootstrap import greeting_seed
from narranexus.platform.bootstrap.lifecycle import BootstrapStatus


def _agent_repo_returning(agent):
    class _Repo:
        def __init__(self, db):
            pass

        async def get_agent(self, agent_id):
            return agent

    return _Repo


def _agent(metadata, created_by="u1"):
    return SimpleNamespace(agent_metadata=metadata, created_by=created_by)


def _status(active):
    return BootstrapStatus(
        active=active, present=active, event_count=0, threshold=3, bootstrap_path="/x/Bootstrap.md"
    )


@pytest.mark.asyncio
async def test_returns_greeting_when_owner_bootstrapping(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "Hi, I'm Echo!"})),
    )
    monkeypatch.setattr(greeting_seed, "is_bootstrap_active", AsyncMock(return_value=_status(True)))

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got == "Hi, I'm Echo!"


@pytest.mark.asyncio
async def test_none_when_bootstrap_expired(monkeypatch):
    """Greeting metadata is permanent; the gate must expire with bootstrap_active
    so a post-auto-delete agent is never re-greeted on a new narrative."""
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(_agent({"bootstrap_greeting": "Hi!"})),
    )
    monkeypatch.setattr(greeting_seed, "is_bootstrap_active", AsyncMock(return_value=_status(False)))

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None


@pytest.mark.asyncio
async def test_none_when_not_owner(monkeypatch):
    monkeypatch.setattr(
        greeting_seed,
        "AgentRepository",
        _agent_repo_returning(
            _agent({"bootstrap_greeting": "Hi!"}, created_by="someone_else")
        ),
    )
    active = AsyncMock(return_value=_status(True))
    monkeypatch.setattr(greeting_seed, "is_bootstrap_active", active)

    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None
    active.assert_not_awaited()  # short-circuits before the expensive check


@pytest.mark.asyncio
async def test_none_when_no_greeting(monkeypatch):
    monkeypatch.setattr(
        greeting_seed, "AgentRepository", _agent_repo_returning(_agent({}))
    )
    monkeypatch.setattr(greeting_seed, "is_bootstrap_active", AsyncMock(return_value=_status(True)))
    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None


@pytest.mark.asyncio
async def test_none_when_agent_missing(monkeypatch):
    monkeypatch.setattr(
        greeting_seed, "AgentRepository", _agent_repo_returning(None)
    )
    got = await greeting_seed.resolve_bootstrap_greeting_to_seed(
        db=object(), agent_id="a1", user_id="u1"
    )
    assert got is None
