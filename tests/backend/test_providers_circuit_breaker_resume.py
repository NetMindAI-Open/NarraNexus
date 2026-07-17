"""
@file_name: test_providers_circuit_breaker_resume.py
@author:
@date: 2026-07-13
@description: providers route auto-resume wrapper delegates to the breaker and
never fails the reconfigure.
"""
import pytest

import xyz_agent_context.agent_framework.agent_circuit_breaker as cb
from backend.routes.providers import _resume_agent_circuit_breakers


@pytest.mark.asyncio
async def test_resume_delegates_to_reset_for_owner(monkeypatch):
    seen = []

    async def fake_reset(user_id, db=None):
        seen.append(user_id)
        return 1
    monkeypatch.setattr(cb, "reset_for_owner", fake_reset)

    await _resume_agent_circuit_breakers("alice")
    assert seen == ["alice"]


@pytest.mark.asyncio
async def test_resume_swallows_errors(monkeypatch):
    async def boom(user_id, db=None):
        raise RuntimeError("db down")
    monkeypatch.setattr(cb, "reset_for_owner", boom)

    # Must NOT raise — provider reconfigure must never fail on breaker resume.
    await _resume_agent_circuit_breakers("bob")
