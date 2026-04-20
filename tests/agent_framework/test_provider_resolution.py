"""
@file_name: test_provider_resolution.py
@author: Bin Liang
@date: 2026-04-20
@description: Per-user provider resolution correctness for Bug 2 refactor.

The new decision tree (replacing the 4-branch version in api_config.py):

  1. `prefer_system_override=True` → strictly use system-default free tier;
     raise `SystemDefaultUnavailable` if disabled or quota exhausted. No
     silent fallback to the user's own provider.
  2. `prefer_system_override=False` (or no quota row) → strictly use the
     user's own providers; raise `LLMConfigNotConfigured` if misconfigured.
     No silent fallback to the system free tier.

Plus `_ensure_quota_service()` lazy-bootstraps `QuotaService.default()` so
every trigger process (Lark, Job, Bus, standalone MCP runner) works
out-of-the-box without calling `bootstrap_quota_subsystem` itself.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework import api_config as api_config_mod
from xyz_agent_context.agent_framework.api_config import (
    LLMConfigNotConfigured,
    SystemDefaultUnavailable,
    LLMResolverError,
    _ensure_quota_service,
    get_user_llm_configs,
)
from xyz_agent_context.agent_framework import quota_service as quota_mod
from xyz_agent_context.agent_framework.quota_service import QuotaService
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)


# -------- Fixtures --------------------------------------------------------

@pytest.fixture
def reset_quota_default():
    """Ensure each test starts with a fresh QuotaService singleton state."""
    prior = QuotaService._default
    QuotaService._default = None
    yield
    QuotaService._default = prior


@pytest.fixture
def reset_system_provider():
    """Reset SystemProviderService singleton between tests."""
    prior = SystemProviderService._instance
    SystemProviderService._instance = None
    yield
    SystemProviderService._instance = prior


@pytest.fixture(autouse=True)
def patch_get_db(monkeypatch, db_client):
    """Redirect get_db_client() to the test's in-memory sqlite fixture so
    both the provider lookup and the lazy quota bootstrap find the seeded
    rows."""
    from xyz_agent_context.utils import db_factory

    async def _fake_get_db():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _fake_get_db)
    yield


@pytest.fixture
def stub_system_provider_enabled(monkeypatch, reset_system_provider):
    """Install a SystemProviderService that reports enabled with a fake config."""
    from xyz_agent_context.schema.provider_schema import (
        LLMConfig, SlotConfig, SlotName, ProviderConfig, ProviderSource,
        ProviderProtocol, AuthType,
    )
    anthropic = ProviderConfig(
        provider_id="system_anthropic",
        name="system",
        source=ProviderSource.NETMIND,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.BEARER_TOKEN,
        api_key="sys_key",
        base_url="https://sys.example/anthropic",
        models=["sys/agent-model"],
        is_active=True,
    )
    openai = ProviderConfig(
        provider_id="system_openai",
        name="system",
        source=ProviderSource.NETMIND,
        protocol=ProviderProtocol.OPENAI,
        auth_type=AuthType.API_KEY,
        api_key="sys_key",
        base_url="https://sys.example/openai",
        models=["sys/embed-model", "sys/helper-model"],
        is_active=True,
    )
    cfg = LLMConfig(
        providers={"system_anthropic": anthropic, "system_openai": openai},
        slots={
            SlotName.AGENT.value: SlotConfig(
                provider_id="system_anthropic", model="sys/agent-model"
            ),
            SlotName.EMBEDDING.value: SlotConfig(
                provider_id="system_openai", model="sys/embed-model"
            ),
            SlotName.HELPER_LLM.value: SlotConfig(
                provider_id="system_openai", model="sys/helper-model"
            ),
        },
    )
    sp = SystemProviderService(enabled=True, config=cfg)
    monkeypatch.setattr(SystemProviderService, "_instance", sp)
    yield sp


@pytest.fixture
def stub_system_provider_disabled(monkeypatch, reset_system_provider):
    sp = SystemProviderService(enabled=False, config=None)
    monkeypatch.setattr(SystemProviderService, "_instance", sp)
    yield sp


# -------- Helpers ---------------------------------------------------------

async def _seed_quota(db, user_id: str, *, opted_in: bool, input_budget: int, output_budget: int):
    """Insert a quota row for a user with the given preference + budget."""
    now = "2026-04-20T00:00:00"
    await db.insert(
        "user_quotas",
        {
            "user_id": user_id,
            "initial_input_tokens": input_budget,
            "initial_output_tokens": output_budget,
            "used_input_tokens": 0,
            "used_output_tokens": 0,
            "granted_input_tokens": 0,
            "granted_output_tokens": 0,
            "status": "active",
            "prefer_system_override": 1 if opted_in else 0,
            "created_at": now,
            "updated_at": now,
        },
    )


async def _seed_full_own_providers(db, user_id: str):
    """Give user A/ helper / embedding slots + matching active providers."""
    now = "2026-04-20T00:00:00"
    import json as _json
    for pid, proto in [
        ("prov_agent", "anthropic"),
        ("prov_openai", "openai"),
    ]:
        await db.insert(
            "user_providers",
            {
                "user_id": user_id,
                "provider_id": pid,
                "name": pid,
                "source": "user",
                "protocol": proto,
                "auth_type": "api_key",
                "api_key": "sk-fake",
                "base_url": "",
                "models": _json.dumps([]),
                "linked_group": "",
                "is_active": 1,
                "supports_anthropic_server_tools": 0,
                "created_at": now,
                "updated_at": now,
            },
        )
    for slot_name, pid, model in [
        ("agent", "prov_agent", "claude-fake"),
        ("helper_llm", "prov_openai", "gpt-fake"),
        ("embedding", "prov_openai", "text-embedding-fake"),
    ]:
        await db.insert(
            "user_slots",
            {
                "user_id": user_id,
                "slot_name": slot_name,
                "provider_id": pid,
                "model": model,
                "updated_at": now,
            },
        )


async def _install_quota_service(db):
    """Set QuotaService.default() to a real instance backed by db."""
    from xyz_agent_context.repository.quota_repository import QuotaRepository
    svc = QuotaService(
        repo=QuotaRepository(db),
        system_provider=SystemProviderService.instance(),
    )
    QuotaService.set_default(svc)
    return svc


# -------- Branch 1: opted-in, strict system path --------------------------

@pytest.mark.asyncio
async def test_opted_in_with_quota_returns_system_default(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=1000, output_budget=1000)

    claude, openai_cfg, emb = await get_user_llm_configs("alice")
    # Model names come from the stubbed system config
    assert claude.model == "sys/agent-model"
    assert claude.api_key == "sys_key"
    assert emb.model == "sys/embed-model"


@pytest.mark.asyncio
async def test_opted_in_with_exhausted_quota_raises_system_unavailable(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=0, output_budget=0)

    with pytest.raises(SystemDefaultUnavailable, match="quota"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_opted_in_but_system_disabled_raises_system_unavailable(
    db_client, stub_system_provider_disabled, reset_quota_default
):
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=1000, output_budget=1000)

    with pytest.raises(SystemDefaultUnavailable, match="(disabled|enabled|administrator)"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_opted_in_does_not_fallback_to_own_config(
    db_client, stub_system_provider_disabled, reset_quota_default
):
    """Even if user has their own provider, opt-in honours the choice."""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=1000, output_budget=1000)
    await _seed_full_own_providers(db_client, "alice")

    with pytest.raises(SystemDefaultUnavailable):
        await get_user_llm_configs("alice")


# -------- Branch 2: opted-out, strict own path ----------------------------

@pytest.mark.asyncio
async def test_opted_out_with_own_config_returns_own(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=False, input_budget=1000, output_budget=1000)
    await _seed_full_own_providers(db_client, "alice")

    claude, openai_cfg, emb = await get_user_llm_configs("alice")
    assert claude.model == "claude-fake"
    assert claude.api_key == "sk-fake"


@pytest.mark.asyncio
async def test_opted_out_without_own_config_raises_not_configured(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """Crucial: opted out ⇒ no silent fallback to free tier even if available."""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=False, input_budget=1000, output_budget=1000)

    with pytest.raises(LLMConfigNotConfigured, match="slot"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_no_quota_row_behaves_as_opted_out(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    # No quota row seeded
    await _seed_full_own_providers(db_client, "alice")

    claude, _, _ = await get_user_llm_configs("alice")
    assert claude.model == "claude-fake"


# -------- Lazy bootstrap --------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_quota_service_lazy_bootstraps(
    db_client, stub_system_provider_enabled, reset_quota_default,
):
    """QuotaService.default() not set → _ensure_quota_service self-bootstraps."""
    assert QuotaService._default is None

    svc = await _ensure_quota_service()
    assert svc is not None
    assert QuotaService._default is svc


@pytest.mark.asyncio
async def test_ensure_quota_service_is_idempotent(
    db_client, stub_system_provider_enabled, reset_quota_default,
):
    first = await _ensure_quota_service()
    second = await _ensure_quota_service()
    assert first is second  # same singleton


# -------- Error hierarchy --------------------------------------------------

def test_error_hierarchy_shares_base():
    assert issubclass(LLMConfigNotConfigured, LLMResolverError)
    assert issubclass(SystemDefaultUnavailable, LLMResolverError)


# -------- tiny util -------------------------------------------------------

def _async_return(value):
    async def _f():
        return value
    return _f
