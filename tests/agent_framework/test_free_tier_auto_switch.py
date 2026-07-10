"""
@file_name: test_free_tier_auto_switch.py
@author: NarraNexus
@date: 2026-07-07
@description: #48 — free-tier→own-key auto-switch: the one-time inbox notice
and the convergence of the agent-run config resolver onto ProviderResolver.

Two seams that the mocked ProviderResolver unit tests can't cover:

1. `_emit_free_tier_switch_notice` actually writes a SYSTEM_NOTICE row against
   the real inbox schema (best-effort, must never raise into the caller).
2. `get_user_runtime_llm_configs` translates the resolver's ProviderResolverError
   vocabulary back into the LLMResolverError family that the agent runtime and
   the job/lark triggers already handle (they string-match the class name), so
   converging the two decision trees doesn't silently break error handling.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.agent_framework import provider_resolver as pr
from xyz_agent_context.agent_framework.provider_resolver import (
    NoProviderConfiguredError,
    QuotaExceededError,
)
from xyz_agent_context.repository.inbox_repository import InboxRepository


# ---------- 1. the one-time notice writes a real inbox row ----------------

@pytest.mark.asyncio
async def test_emit_free_tier_switch_notice_writes_system_notice(db_client):
    await pr._emit_free_tier_switch_notice(db_client, "usr_note")

    repo = InboxRepository(db_client)
    msgs = await repo.get_messages("usr_note", limit=10)
    assert len(msgs) == 1
    assert msgs[0].message_type.value == "system"
    assert (await repo.get_unread_count("usr_note")) == 1


@pytest.mark.asyncio
async def test_emit_free_tier_switch_notice_swallows_failure(db_client):
    """The notice is a courtesy: a broken inbox write must not propagate and
    kill the run that just auto-switched to the user's key."""
    class _BoomDB:
        def __getattr__(self, _):
            raise RuntimeError("inbox down")

    # Must not raise.
    await pr._emit_free_tier_switch_notice(_BoomDB(), "usr_boom")


# ---------- 2. api_config translates resolver errors ----------------------

async def _patch_resolver(monkeypatch, *, resolve):
    """Point get_user_runtime_llm_configs at a stub ProviderResolver whose
    resolve() does whatever the test needs, and stub the wiring it constructs
    (db factory, services, quota bootstrap) so no real DB/env is required."""
    import xyz_agent_context.agent_framework.api_config as api_config

    class _StubResolver:
        def __init__(self, **_kw):
            pass

        async def resolve(self, user_id, agent_id=None):
            return await resolve(user_id)

    monkeypatch.setattr(pr, "ProviderResolver", _StubResolver)
    # api_config imports these names lazily inside the function; stub the
    # cheap wiring so construction doesn't need a live DB / provider env.
    monkeypatch.setattr(
        api_config, "_ensure_quota_service", AsyncMock(return_value=object())
    )
    from xyz_agent_context.utils import db_factory
    monkeypatch.setattr(db_factory, "get_db_client", AsyncMock(return_value=object()))
    from xyz_agent_context.agent_framework import system_provider_service, user_provider_service
    monkeypatch.setattr(
        system_provider_service.SystemProviderService, "instance",
        staticmethod(lambda: object()),
    )
    monkeypatch.setattr(user_provider_service, "UserProviderService", lambda db: object())
    return api_config


@pytest.mark.asyncio
async def test_quota_exceeded_translated_to_system_default_unavailable(monkeypatch):
    """Opted in, exhausted, no own provider → QuotaExceededError must surface
    to the agent-run path as SystemDefaultUnavailable (an LLMResolverError the
    runtime catches; job/lark triggers match on that exact class name)."""
    async def _resolve(_uid, agent_id=None):
        raise QuotaExceededError("usr_x")

    api_config = await _patch_resolver(monkeypatch, resolve=_resolve)
    with pytest.raises(api_config.SystemDefaultUnavailable):
        await api_config.get_user_runtime_llm_configs("usr_x")


@pytest.mark.asyncio
async def test_no_provider_translated_to_llm_config_not_configured(monkeypatch):
    async def _resolve(_uid, agent_id=None):
        raise NoProviderConfiguredError("usr_x")

    api_config = await _patch_resolver(monkeypatch, resolve=_resolve)
    with pytest.raises(api_config.LLMConfigNotConfigured):
        await api_config.get_user_runtime_llm_configs("usr_x")


@pytest.mark.asyncio
async def test_translated_errors_are_llm_resolver_errors(monkeypatch):
    """Both translations land in the LLMResolverError family so a single
    `except LLMResolverError` in agent_runtime catches them."""
    async def _resolve(_uid, agent_id=None):
        raise QuotaExceededError("usr_x")

    api_config = await _patch_resolver(monkeypatch, resolve=_resolve)
    with pytest.raises(api_config.LLMResolverError):
        await api_config.get_user_runtime_llm_configs("usr_x")
