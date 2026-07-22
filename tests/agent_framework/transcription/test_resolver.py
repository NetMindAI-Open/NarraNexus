"""
@file_name: test_resolver.py
@description: Resolver candidate priority + cloud/local guards

Verifies the 5-tier ordering documented in the design doc §7:

    1. user OpenAI official
    2. user NetMind
    3. user other OpenAI-multipart compatible (Yunwu / self-hosted)
    4. settings.openai_api_key (local mode only)
    5. system-default NetMind (cloud free tier)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.agent_framework.transcription import resolver as R
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
)
from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _provider(
    base_url: str,
    *,
    source: ProviderSource = ProviderSource.USER,
    api_key: str = "sk-x",
    is_active: bool = True,
    protocol: ProviderProtocol = ProviderProtocol.OPENAI,
) -> ProviderConfig:
    return ProviderConfig(
        provider_id=f"p_{abs(hash(base_url)) % 1_000_000}",
        name="test",
        source=source,
        protocol=protocol,
        auth_type=AuthType.API_KEY,
        api_key=api_key,
        base_url=base_url,
        is_active=is_active,
    )


def _patch_user_providers(monkeypatch, *providers):
    """Replace the UserProviderService.get_user_config import path so the
    resolver returns the providers we want. We monkeypatch the imported
    module the resolver uses (the inner-function import resolves at
    runtime, so we patch the canonical module attribute)."""

    fake_cfg = LLMConfig(
        providers={p.provider_id: p for p in providers},
        slots={},
    )

    fake_svc = MagicMock()
    fake_svc.get_user_config = AsyncMock(return_value=fake_cfg)

    fake_module = MagicMock()
    fake_module.UserProviderService = MagicMock(return_value=fake_svc)

    fake_db_module = MagicMock()
    fake_db_module.get_db_client = AsyncMock(return_value=MagicMock())

    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.user_provider_service.UserProviderService",
        fake_module.UserProviderService,
    )
    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client",
        fake_db_module.get_db_client,
    )


def _patch_local_mode(monkeypatch, is_cloud: bool):
    monkeypatch.setattr(
        "xyz_agent_context.utils.deployment_mode.is_cloud_mode",
        lambda: is_cloud,
    )


def _patch_free_tier(
    monkeypatch,
    *,
    system_enabled: bool = True,
    quota_pref: bool | None = True,
    has_budget: bool = True,
):
    """Stub the free-tier grant gate.

    ``system_enabled`` simulates whether SystemProviderService.is_enabled()
    answers True (cloud-mode + SYSTEM_DEFAULT_LLM_ENABLED). ``quota_pref``
    sets the row's ``prefer_system_override`` (the exhaustion-notice latch —
    must NOT affect routing since 2026-07-18); ``None`` means "no quota row
    for this user". The gate denies the system tier on no-row OR no-budget
    (``has_budget`` — STT shares the LLM path's budget verdict).
    """
    fake_sys = MagicMock()
    fake_sys.is_enabled.return_value = system_enabled
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.system_provider_service.SystemProviderService.instance",
        classmethod(lambda cls: fake_sys),
    )

    fake_quota_row = None if quota_pref is None else MagicMock(prefer_system_override=quota_pref)
    fake_qs = MagicMock()
    fake_qs.get = AsyncMock(return_value=fake_quota_row)
    fake_qs.check = AsyncMock(return_value=has_budget)
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.quota_service.QuotaService.default",
        classmethod(lambda cls: fake_qs),
    )


def _patch_settings(monkeypatch, **kwargs):
    """Override resolver.settings fields. Defaults clear all transcription-
    relevant fields so each test starts from a clean slate."""
    defaults = {
        "openai_api_key": "",
        "system_default_netmind_api_key": "",
        "system_default_netmind_base_url": "https://api.netmind.ai",
        "public_base_url": "",
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        monkeypatch.setattr(R.settings, k, v)


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_providers_no_settings_returns_empty(monkeypatch):
    _patch_user_providers(monkeypatch)
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch)
    _patch_free_tier(monkeypatch, system_enabled=False)
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_user_openai_official_first(monkeypatch):
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", api_key="sk-openai-official"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch)
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.OPENAI_MULTIPART
    assert creds[0].api_key == "sk-openai-official"
    assert "openai_official" in creds[0].source_tag


@pytest.mark.asyncio
async def test_user_netmind_aggregator_becomes_native_credential(monkeypatch):
    """User configures `https://api.netmind.ai/inference-api/openai/v1` —
    the resolver rewrites the base_url to the native /v1/generation root.
    Requires PUBLIC_BASE_URL so the NetMind worker has a fetchable URL."""
    _patch_user_providers(
        monkeypatch,
        _provider(
            "https://api.netmind.ai/inference-api/openai/v1",
            source=ProviderSource.NETMIND,
            api_key="netmind-key",
        ),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="https://my-deploy.example.com")
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.NETMIND
    assert creds[0].api_key == "netmind-key"
    assert creds[0].base_url == "https://api.netmind.ai"
    assert creds[0].model == "openai/whisper"
    assert creds[0].is_system_free_tier is False


@pytest.mark.asyncio
async def test_local_mode_skips_user_netmind_credential(monkeypatch):
    """No PUBLIC_BASE_URL ⇒ NetMind worker can't fetch our audio.
    Skip the user-configured NetMind credential cleanly rather than
    serving it up to fail later."""
    _patch_user_providers(
        monkeypatch,
        _provider(
            "https://api.netmind.ai/inference-api/openai/v1",
            source=ProviderSource.NETMIND,
            api_key="user-netmind",
        ),
    )
    _patch_local_mode(monkeypatch, is_cloud=False)
    _patch_free_tier(monkeypatch, system_enabled=False)
    _patch_settings(monkeypatch, public_base_url="")
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_local_mode_user_openai_still_works_when_netmind_skipped(monkeypatch):
    """Skipping NetMind doesn't affect the OpenAI multipart path —
    OpenAI sends bytes directly, no public ingress needed."""
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", api_key="user-openai"),
        _provider(
            "https://api.netmind.ai/inference-api/openai/v1",
            source=ProviderSource.NETMIND,
            api_key="user-netmind",
        ),
    )
    _patch_local_mode(monkeypatch, is_cloud=False)
    _patch_free_tier(monkeypatch, system_enabled=False)
    _patch_settings(monkeypatch, public_base_url="")
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.OPENAI_MULTIPART
    assert creds[0].api_key == "user-openai"


@pytest.mark.asyncio
async def test_self_hosted_with_public_base_url_re_enables_netmind(monkeypatch):
    """User self-deploys the backend on their own VPS and sets
    PUBLIC_BASE_URL — NetMind credential becomes viable again, regardless
    of cloud/local mode flag."""
    _patch_user_providers(
        monkeypatch,
        _provider(
            "https://api.netmind.ai/inference-api/openai/v1",
            source=ProviderSource.NETMIND,
            api_key="user-netmind",
        ),
    )
    _patch_local_mode(monkeypatch, is_cloud=False)
    _patch_free_tier(monkeypatch, system_enabled=False)
    _patch_settings(monkeypatch, public_base_url="https://my-vps.example.com")
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.NETMIND


@pytest.mark.asyncio
async def test_priority_openai_official_beats_netmind_beats_yunwu(monkeypatch):
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.yunwuai.cloud/v1", api_key="yunwu-key"),
        _provider(
            "https://api.netmind.ai/inference-api/openai/v1",
            source=ProviderSource.NETMIND,
            api_key="netmind-key",
        ),
        _provider("https://api.openai.com/v1", api_key="openai-key"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="https://my-deploy.example.com")
    creds = await R.resolve_candidates(user_id="u1")
    # Three candidates, in this order
    assert [c.backend_kind for c in creds] == [
        TranscriptionBackendKind.OPENAI_MULTIPART,
        TranscriptionBackendKind.NETMIND,
        TranscriptionBackendKind.OPENAI_MULTIPART,
    ]
    assert [c.api_key for c in creds] == ["openai-key", "netmind-key", "yunwu-key"]


@pytest.mark.asyncio
async def test_inactive_provider_skipped(monkeypatch):
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", is_active=False),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch)
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_openrouter_user_provider_skipped(monkeypatch):
    """OpenRouter Whisper is JSON+base64; backend not implemented yet —
    resolver must skip it rather than picking a backend that can't speak
    its protocol."""
    _patch_user_providers(
        monkeypatch,
        _provider("https://openrouter.ai/api/v1", api_key="openrouter-key"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch)
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_settings_openai_only_used_in_local_mode(monkeypatch):
    _patch_user_providers(monkeypatch)
    _patch_settings(monkeypatch, openai_api_key="sk-env")

    # Cloud mode: settings.openai_api_key is an operator key and should
    # NOT be silently used to transcribe random users' audio.
    _patch_local_mode(monkeypatch, is_cloud=True)
    creds_cloud = await R.resolve_candidates(user_id="u1")
    assert creds_cloud == []

    # Local mode: it's THE user's own .env file, fall back to it.
    _patch_local_mode(monkeypatch, is_cloud=False)
    creds_local = await R.resolve_candidates(user_id="u1")
    assert len(creds_local) == 1
    assert creds_local[0].source_tag == "settings.openai"
    assert creds_local[0].backend_kind == TranscriptionBackendKind.OPENAI_MULTIPART


@pytest.mark.asyncio
async def test_system_default_netmind_only_when_public_base_url_set(monkeypatch):
    _patch_user_providers(monkeypatch)
    _patch_local_mode(monkeypatch, is_cloud=True)
    # Free-tier opt-in is on — but missing public_base_url is a hard
    # blocker. The toggle being "yes please" doesn't override deployment
    # config, it just unlocks the free tier when config IS present.
    _patch_free_tier(monkeypatch, quota_pref=True)

    # Key set, base URL unset → resolver downgrades NetMind to unavailable.
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="",
    )
    assert await R.resolve_candidates(user_id="u1") == []

    # Both set + opt-in on → system NetMind is the sole candidate.
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.NETMIND
    assert creds[0].is_system_free_tier is True
    assert creds[0].api_key == "sys-netmind-key"
    assert creds[0].source_tag == "system_default:netmind"


@pytest.mark.asyncio
async def test_user_provider_takes_precedence_over_system_default(monkeypatch):
    """User-configured providers always come before the cloud free tier."""
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", api_key="user-openai"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, quota_pref=True)
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 2
    assert creds[0].api_key == "user-openai"
    assert creds[1].is_system_free_tier is True


# ─────────────────────────────────────────────────────────────────────
# free-tier grant gate (anti-freeloading guard). Since 2026-07-18 the
# gate is "does a quota row exist" — prefer_system_override is only the
# exhaustion-notice latch and must NOT deny routing.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fired_notice_latch_still_gets_system_default(monkeypatch):
    """The notice latch being fired (prefer_system_override=0, i.e. an
    exhaustion cycle happened) must NOT deny the free tier — it is not a
    user preference. Regression guard for the 2026-07-18 semantics."""
    _patch_user_providers(monkeypatch)  # no own providers
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, quota_pref=False)  # latch fired, row EXISTS
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].is_system_free_tier is True


@pytest.mark.asyncio
async def test_fired_latch_own_providers_rank_first(monkeypatch):
    """Own keys keep working and outrank the system tier."""
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", api_key="user-openai"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, quota_pref=False)
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 2
    assert creds[0].api_key == "user-openai"
    assert creds[0].is_system_free_tier is False
    assert creds[1].is_system_free_tier is True


@pytest.mark.asyncio
async def test_exhausted_quota_gets_no_system_default(monkeypatch):
    """Row exists but the budget is gone → NO operator-billed STT. STT usage
    doesn't deduct from user_quotas, so without this gate an exhausted
    account could burn the operator's NetMind STT key indefinitely while
    its LLM path is already blocked (review 2026-07-18) — the two paths
    must share one budget verdict."""
    _patch_user_providers(monkeypatch)
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, quota_pref=True, has_budget=False)
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_no_quota_row_gets_no_system_default(monkeypatch):
    """Brand-new user with no quota row at all → no free tier was granted,
    so STT must not silently bill the operator (implicit-grant liability
    guard — the row IS the grant)."""
    _patch_user_providers(monkeypatch)
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, quota_pref=None)  # no row
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_system_provider_disabled_skips_system_default(monkeypatch):
    """SystemProviderService.is_enabled()==False (local mode or
    SYSTEM_DEFAULT_LLM_ENABLED unset) → system NetMind is unreachable
    regardless of the user's toggle."""
    _patch_user_providers(monkeypatch)
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_free_tier(monkeypatch, system_enabled=False, quota_pref=True)
    _patch_settings(
        monkeypatch,
        system_default_netmind_api_key="sys-netmind-key",
        public_base_url="https://my-deploy.example.com",
    )
    creds = await R.resolve_candidates(user_id="u1")
    assert creds == []


@pytest.mark.asyncio
async def test_no_user_id_skips_user_tier(monkeypatch):
    _patch_user_providers(monkeypatch, _provider("https://api.openai.com/v1"))
    _patch_local_mode(monkeypatch, is_cloud=False)
    _patch_settings(monkeypatch, openai_api_key="env-key")

    creds = await R.resolve_candidates(user_id=None)
    # Only settings.openai is present (local mode), user tier is bypassed
    assert len(creds) == 1
    assert creds[0].source_tag == "settings.openai"


@pytest.mark.asyncio
async def test_user_lookup_failure_does_not_break_resolution(monkeypatch):
    """Database / import failure during user-provider lookup should not
    crash the upload route — fall through to system / settings."""
    async def _explode(*a, **kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", _explode,
    )
    _patch_local_mode(monkeypatch, is_cloud=False)
    _patch_settings(monkeypatch, openai_api_key="env-key")

    creds = await R.resolve_candidates(user_id="u1")
    assert len(creds) == 1
    assert creds[0].source_tag == "settings.openai"
