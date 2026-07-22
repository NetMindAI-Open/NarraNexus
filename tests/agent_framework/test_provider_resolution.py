"""
@file_name: test_provider_resolution.py
@author: Bin Liang
@date: 2026-04-20
@description: Per-user provider resolution correctness for Bug 2 refactor.

Since the #48 convergence, `get_user_llm_configs` delegates to the single
`ProviderResolver` tree (no divergent copy in api_config.py). Behavior:

  1. quota row + budget → system free tier (free-tier-first is platform
     behavior; the opt-out preference was removed 2026-07-18).
     + exhausted + own provider → own key takes over (#48).
     + exhausted + no own provider → `SystemDefaultUnavailable`.
     + free tier disabled (SYSTEM_DISABLED) → passthrough to own config;
       `LLMConfigNotConfigured` only if no own provider exists.
  2. no quota row → strictly the user's own providers;
     `LLMConfigNotConfigured` if misconfigured. No silent fallback to the
     system free tier (implicit-grant liability guard).

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
    get_current_user_id,
    get_provider_source,
    get_user_llm_configs,
    set_current_user_id,
    set_provider_source,
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
    """Give user agent + helper_llm slots + matching active providers.

    Provider models arrays include the slot model so the Phase 0
    reverse-validation self-heal (provider_driver.self_heal) does NOT
    rewrite the slot at resolve time. Without those entries, self_heal
    sees ``claude-fake NOT IN provider.models`` and auto-swaps to the
    catalog default — which breaks these assertions.
    """
    now = "2026-04-20T00:00:00"
    import json as _json
    provider_models = {
        "prov_agent": ["claude-fake"],
        "prov_openai": ["gpt-fake", "text-embedding-fake"],
    }
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
                "models": _json.dumps(provider_models[pid]),
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

    claude, openai_cfg = await get_user_llm_configs("alice")
    # Model names come from the stubbed system config
    assert claude.model == "sys/agent-model"
    assert claude.api_key == "sys_key"


@pytest.mark.asyncio
async def test_opted_in_with_exhausted_quota_raises_system_unavailable(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=0, output_budget=0)

    with pytest.raises(SystemDefaultUnavailable, match="quota"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_opted_in_but_system_disabled_without_own_raises_not_configured(
    db_client, stub_system_provider_disabled, reset_quota_default
):
    """#48 convergence: a disabled free tier is SYSTEM_DISABLED (local/desktop
    mode) — the resolver passes through and the agent-run path falls to strict
    own-config. With no own provider that surfaces LLMConfigNotConfigured
    (missing slots), not SystemDefaultUnavailable. Both are actionable
    "configure a provider" errors; the type just follows the single tree now."""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=1000, output_budget=1000)

    with pytest.raises(LLMConfigNotConfigured, match="slot"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_opted_in_but_system_disabled_falls_through_to_own_config(
    db_client, stub_system_provider_disabled, reset_quota_default
):
    """#48 convergence: when the free tier is disabled (SYSTEM_DISABLED) and the
    user has a complete own provider, the run uses that provider rather than
    hard-erroring. This matches the resolver's passthrough semantics — the two
    decision trees now agree. (Distinct from an EXHAUSTED free tier, which
    auto-switches with a one-time notice; see test_free_tier_auto_switch.)"""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=1000, output_budget=1000)
    await _seed_full_own_providers(db_client, "alice")

    claude, _ = await get_user_llm_configs("alice")
    assert claude.model == "claude-fake"
    assert claude.api_key == "sk-fake"


# -------- Branch 2: opted-out, strict own path ----------------------------

@pytest.mark.asyncio
async def test_exhausted_free_tier_with_own_config_returns_own(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """user_pays path in the new world: the free tier is always drawn first
    (no opt-out preference since 2026-07-18), so a user runs on their own
    key exactly when the free tier is exhausted."""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=0, output_budget=0)
    await _seed_full_own_providers(db_client, "alice")

    claude, openai_cfg = await get_user_llm_configs("alice")
    assert claude.model == "claude-fake"
    assert claude.api_key == "sk-fake"


# -------- Billing boundary: user_pays must NEVER touch the free tier --------
#
# Prod incident (2026-07-07): opted-out (user_pays) users had their free-tier
# quota drained to 8-30x the cap. Root cause: the deduct in cost_tracker fires
# only when the `provider_source` ContextVar == "system", and the OLD
# get_user_runtime_llm_configs else-branch (prefer_system_override=False) never
# called set_provider_source — so a stale "system" left by a prior run leaked
# in and made the user's OWN-KEY consumption debit the free tier. The #48
# convergence fixed it by always tagging the source. These tests pin the whole
# accounting boundary end-to-end so it cannot regress.


@pytest.fixture(autouse=True)
def _reset_billing_ctx():
    set_provider_source(None)
    set_current_user_id(None)
    yield
    set_provider_source(None)
    set_current_user_id(None)


@pytest.mark.asyncio
async def test_user_pays_resolution_retags_source_over_stale_system(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """The regression that WOULD have caught the leak: a user_pays resolution
    (exhausted free tier + own key) must overwrite a stale "system" left in
    the ContextVar with "user"."""
    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=0, output_budget=0)
    await _seed_full_own_providers(db_client, "alice")

    set_provider_source("system")  # simulate a leak from a prior system run
    await get_user_llm_configs("alice")
    assert get_provider_source() == "user"  # retagged — NOT stale "system"


@pytest.mark.asyncio
async def test_user_pays_consumption_never_deducts_free_tier(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """End-to-end: resolve a user_pays user (exhausted free tier + own key,
    even with a stale "system" leak in place), then record a costly call —
    the free-tier quota must not move by a single token."""
    from xyz_agent_context.repository.quota_repository import QuotaRepository
    from xyz_agent_context.utils.cost_tracker import record_cost

    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=0, output_budget=0)
    await _seed_full_own_providers(db_client, "alice")

    set_provider_source("system")  # stale leak
    await get_user_llm_configs("alice")  # retags to "user"
    set_current_user_id("alice")
    await record_cost(
        db=db_client, agent_id="ag", event_id=None, call_type="agent_loop",
        model="claude-fake", input_tokens=500, output_tokens=200,
    )

    q = await QuotaRepository(db_client).get_by_user_id("alice")
    assert q.used_input_tokens == 0
    assert q.used_output_tokens == 0


@pytest.mark.asyncio
async def test_system_consumption_does_deduct_free_tier(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """The other side of the boundary: a genuine free-tier (system) call DOES
    debit the quota — so the fix didn't over-correct into never charging."""
    from xyz_agent_context.repository.quota_repository import QuotaRepository
    from xyz_agent_context.utils.cost_tracker import record_cost

    await _install_quota_service(db_client)
    await _seed_quota(db_client, "alice", opted_in=True, input_budget=100_000, output_budget=100_000)

    await get_user_llm_configs("alice")  # opted-in + budget → system
    assert get_provider_source() == "system"
    assert get_current_user_id() == "alice"
    await record_cost(
        db=db_client, agent_id="ag", event_id=None, call_type="agent_loop",
        model="claude-fake", input_tokens=500, output_tokens=200,
    )

    q = await QuotaRepository(db_client).get_by_user_id("alice")
    assert q.used_input_tokens == 500
    assert q.used_output_tokens == 200


@pytest.mark.asyncio
async def test_no_quota_row_without_own_config_raises_not_configured(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    """Crucial: no quota row ⇒ no silent fallback to the free tier (the
    implicit-grant liability guard). Via the converged tree this surfaces as
    NoProviderConfiguredError, mapped to LLMConfigNotConfigured."""
    await _install_quota_service(db_client)
    # deliberately NO quota row seeded

    with pytest.raises(LLMConfigNotConfigured, match="[Pp]rovider"):
        await get_user_llm_configs("alice")


@pytest.mark.asyncio
async def test_no_quota_row_behaves_as_opted_out(
    db_client, stub_system_provider_enabled, reset_quota_default
):
    await _install_quota_service(db_client)
    # No quota row seeded
    await _seed_full_own_providers(db_client, "alice")

    claude, _ = await get_user_llm_configs("alice")
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
