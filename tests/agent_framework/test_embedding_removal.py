"""
@file_name: test_embedding_removal.py
@author: Bin Liang
@date: 2026-06-04
@description: Verify that EmbeddingConfig / embedding slot are removed from
the provider configuration system. The config tuple is now (ClaudeConfig,
OpenAIConfig) — no embedding element.
"""
from __future__ import annotations

import pytest


# ── 1. EmbeddingConfig must NOT be importable from api_config ────────────────

def test_embedding_config_not_exported():
    import xyz_agent_context.agent_framework.api_config as m
    assert not hasattr(m, "EmbeddingConfig"), (
        "EmbeddingConfig should have been removed from api_config"
    )


def test_embedding_config_proxy_not_exported():
    import xyz_agent_context.agent_framework.api_config as m
    assert not hasattr(m, "embedding_config"), (
        "embedding_config proxy should have been removed from api_config"
    )


def test_get_current_embedding_config_not_exported():
    import xyz_agent_context.agent_framework.api_config as m
    assert not hasattr(m, "get_current_embedding_config"), (
        "get_current_embedding_config should have been removed from api_config"
    )


# ── 2. set_user_config accepts exactly 2 args (claude, openai) ───────────────

def test_set_user_config_accepts_2_args():
    """set_user_config must take (claude, openai) — NOT a 3rd embedding arg."""
    import inspect
    from xyz_agent_context.agent_framework.api_config import set_user_config, ClaudeConfig, OpenAIConfig

    sig = inspect.signature(set_user_config)
    params = list(sig.parameters)
    assert params == ["claude", "openai"], (
        f"set_user_config signature must be (claude, openai), got {params}"
    )


# ── 3. get_user_llm_configs returns a 2-tuple ────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_llm_configs_returns_2_tuple(db_client, monkeypatch):
    """get_user_llm_configs must return (ClaudeConfig, OpenAIConfig)."""
    import json as _json
    from xyz_agent_context.agent_framework.api_config import (
        get_user_llm_configs,
        ClaudeConfig,
        OpenAIConfig,
    )
    from xyz_agent_context.utils import db_factory
    from xyz_agent_context.agent_framework.quota_service import QuotaService, bootstrap_quota_subsystem
    from xyz_agent_context.agent_framework.system_provider_service import SystemProviderService

    # Patch db so the lazy bootstrap finds the in-memory db.
    async def _fake_db():
        return db_client
    monkeypatch.setattr(db_factory, "get_db_client", _fake_db)

    # Disable system provider so we go through the user-owns path.
    SystemProviderService._instance = SystemProviderService(enabled=False, config=None)

    # Seed providers + slots.
    now = "2026-06-04T00:00:00"
    for pid, proto in [("prov_a", "anthropic"), ("prov_o", "openai")]:
        await db_client.insert("user_providers", {
            "user_id": "alice",
            "provider_id": pid,
            "name": pid,
            "source": "user",
            "protocol": proto,
            "auth_type": "api_key",
            "api_key": "sk-fake",
            "base_url": "",
            "models": _json.dumps(["claude-fake"] if proto == "anthropic" else ["gpt-fake"]),
            "linked_group": "",
            "is_active": 1,
            "supports_anthropic_server_tools": 0,
            "created_at": now,
            "updated_at": now,
        })
    for slot_name, pid, model in [
        ("agent", "prov_a", "claude-fake"),
        ("helper_llm", "prov_o", "gpt-fake"),
    ]:
        await db_client.insert("user_slots", {
            "user_id": "alice",
            "slot_name": slot_name,
            "provider_id": pid,
            "model": model,
            "updated_at": now,
        })

    # Bootstrap quota service so _ensure_quota_service() finds it.
    qs = await bootstrap_quota_subsystem(db_client)
    QuotaService.set_default(qs)

    result = await get_user_llm_configs("alice")
    assert len(result) == 2, f"Expected 2-tuple, got length {len(result)}"
    claude, openai = result
    assert isinstance(claude, ClaudeConfig)
    assert isinstance(openai, OpenAIConfig)

    # Cleanup
    SystemProviderService._instance = None
    QuotaService._default = None


# ── 4. _REQUIRED_SLOTS in provider_resolver does NOT include "embedding" ─────

def test_required_slots_no_embedding():
    from xyz_agent_context.agent_framework import provider_resolver
    assert "embedding" not in provider_resolver._REQUIRED_SLOTS, (
        "_REQUIRED_SLOTS must not contain 'embedding'"
    )


# ── 5. SlotName.EMBEDDING may stay in schema (DB compat), but
#    SLOT_REQUIRED_PROTOCOLS must not map it ──────────────────────────────────

def test_slot_required_protocols_no_embedding():
    from xyz_agent_context.schema.provider_schema import SLOT_REQUIRED_PROTOCOLS, SlotName
    assert not hasattr(SlotName, "EMBEDDING"), "SlotName.EMBEDDING must be removed"
    assert "embedding" not in {getattr(s, "value", s) for s in SLOT_REQUIRED_PROTOCOLS}, (
        "SLOT_REQUIRED_PROTOCOLS must not have an EMBEDDING entry"
    )


# ── 6. provider_driver.resolver._SLOT_BUILDERS has no "embedding" key ────────

def test_slot_builders_no_embedding():
    from xyz_agent_context.agent_framework.provider_driver.resolver import _SLOT_BUILDERS
    assert "embedding" not in _SLOT_BUILDERS, (
        "_SLOT_BUILDERS in provider_driver.resolver must not contain 'embedding'"
    )


# ── 7. resolve() in ProviderResolver returns 3-tuple (claude, openai, source)
#    not 4-tuple (claude, openai, embedding, source) ──────────────────────────

@pytest.mark.asyncio
async def test_provider_resolver_resolve_returns_3_tuple():
    """ProviderResolver.resolve must return (claude, openai, source) — 3 items."""
    from unittest.mock import AsyncMock, MagicMock
    from xyz_agent_context.agent_framework.provider_resolver import ProviderResolver
    from xyz_agent_context.schema.provider_schema import (
        AuthType, LLMConfig, ProviderConfig, ProviderProtocol, ProviderSource, SlotConfig,
    )

    prov_a = ProviderConfig(
        provider_id="p_a", name="a", source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC, auth_type=AuthType.API_KEY,
        api_key="k", is_active=True, models=["claude-x"],
    )
    prov_o = ProviderConfig(
        provider_id="p_o", name="o", source=ProviderSource.USER,
        protocol=ProviderProtocol.OPENAI, auth_type=AuthType.API_KEY,
        api_key="k", is_active=True, models=["gpt-x"],
    )
    cfg = LLMConfig(
        providers={"p_a": prov_a, "p_o": prov_o},
        slots={
            "agent": SlotConfig(provider_id="p_a", model="claude-x"),
            "helper_llm": SlotConfig(provider_id="p_o", model="gpt-x"),
        },
    )
    user_svc = MagicMock()
    user_svc.get_user_config = AsyncMock(return_value=cfg)
    sys_svc = MagicMock()
    sys_svc.is_enabled.return_value = True
    # System config also only has agent + helper_llm slots.
    sys_svc.get_config.return_value = cfg
    quota_svc = MagicMock()
    quota_svc.get = AsyncMock(return_value=None)      # no quota row → opt-out
    quota_svc.check = AsyncMock(return_value=True)

    resolver = ProviderResolver(user_svc, sys_svc, quota_svc)
    result = await resolver.resolve("usr_x")
    # result must be (claude, openai, source) — exactly 3 elements
    assert result is not None
    assert len(result) == 3, f"Expected 3-tuple from ProviderResolver.resolve, got {len(result)}"
