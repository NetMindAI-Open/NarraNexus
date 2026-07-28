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

from xyz_agent_context.agent_framework.llm.transcription import resolver as R
from xyz_agent_context.agent_framework.llm.transcription.credential import (
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
        "xyz_agent_context.agent_framework.providers.user_service.UserProviderService",
        fake_module.UserProviderService,
    )
    monkeypatch.setattr(
        "xyz_agent_context.utils.db.db_factory.get_db_client",
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
    wallet_configured: bool = True,
    has_wallet: bool = True,
    has_budget: bool = True,
):
    """Stub the free-tier grant gate.

    The gate is now "does this user have a free-tier WALLET with money left",
    answered by the deploy-side wallet service. ``wallet_configured=False``
    simulates a deployment with no free tier at all (local / feature off);
    ``has_wallet=False`` a user who was never provisioned; ``has_budget=False``
    a spent wallet.

    STT deliberately shares the LLM path's budget verdict: transcription does
    NOT flow through the gateway (it calls NetMind STT with the operator key),
    so nothing upstream can refuse it — without this check an exhausted account
    would keep burning the operator's STT key after its LLM path is blocked.
    """
    from xyz_agent_context.integrations.free_tier import wallet_client as wc

    if not wallet_configured:
        monkeypatch.setattr(
            wc.WalletClient, "from_settings", classmethod(lambda cls: None)
        )
        return

    fake = MagicMock()
    if has_wallet:
        fake.balance = AsyncMock(return_value=wc.WalletBalance(
            currency="USD", max_budget=10.0,
            spend=10.0 if not has_budget else 1.0,
            remaining=0.0 if not has_budget else 9.0,
            exhausted=not has_budget,
        ))
    else:
        fake.balance = AsyncMock(side_effect=wc.WalletMissing("none"))
    monkeypatch.setattr(
        wc.WalletClient, "from_settings", classmethod(lambda cls: fake)
    )


def _patch_settings(monkeypatch, **kwargs):
    """Override resolver.settings fields. Defaults clear all transcription-
    relevant fields so each test starts from a clean slate."""
    defaults = {
        "openai_api_key": "",
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
    _patch_free_tier(monkeypatch, wallet_configured=False)
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
    _patch_free_tier(monkeypatch, wallet_configured=False)
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
    _patch_free_tier(monkeypatch, wallet_configured=False)
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
    _patch_free_tier(monkeypatch, wallet_configured=False)
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
async def test_free_tier_card_routes_through_the_stt_proxy(monkeypatch):
    """The free-tier card's own key IS the credential — the operator's NetMind
    key is no longer part of this process at all (2026-07-28)."""
    _patch_user_providers(
        monkeypatch,
        _provider("http://litellm:4000/v1", api_key="sk-wallet", source="netmind_free"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="https://my-deploy.example.com")
    monkeypatch.setenv("FREE_TIER_STT_PROXY_URL", "http://quota-api:8110")

    creds = await R.resolve_candidates(user_id="u1")

    assert len(creds) == 1
    assert creds[0].backend_kind == TranscriptionBackendKind.GATEWAY
    assert creds[0].api_key == "sk-wallet"       # the user's wallet key
    assert creds[0].base_url == "http://quota-api:8110"
    assert creds[0].source_tag == "free_tier:gateway"


@pytest.mark.asyncio
async def test_free_tier_card_needs_public_ingress(monkeypatch):
    """The proxy forwards a URL, it does not carry bytes — NetMind still PULLS
    the audio, so an unreachable deployment must not offer this candidate."""
    _patch_user_providers(
        monkeypatch,
        _provider("http://litellm:4000/v1", api_key="sk-wallet", source="netmind_free"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="")
    monkeypatch.setenv("FREE_TIER_STT_PROXY_URL", "http://quota-api:8110")

    assert await R.resolve_candidates(user_id="u1") == []


@pytest.mark.asyncio
async def test_free_tier_card_is_skipped_when_no_proxy_is_configured(monkeypatch):
    _patch_user_providers(
        monkeypatch,
        _provider("http://litellm:4000/v1", api_key="sk-wallet", source="netmind_free"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="https://my-deploy.example.com")
    monkeypatch.delenv("FREE_TIER_STT_PROXY_URL", raising=False)

    assert await R.resolve_candidates(user_id="u1") == []


@pytest.mark.asyncio
async def test_own_provider_still_outranks_the_free_tier(monkeypatch):
    _patch_user_providers(
        monkeypatch,
        _provider("https://api.openai.com/v1", api_key="user-openai"),
        _provider("http://litellm:4000/v1", api_key="sk-wallet", source="netmind_free"),
    )
    _patch_local_mode(monkeypatch, is_cloud=True)
    _patch_settings(monkeypatch, public_base_url="https://my-deploy.example.com")
    monkeypatch.setenv("FREE_TIER_STT_PROXY_URL", "http://quota-api:8110")

    creds = await R.resolve_candidates(user_id="u1")

    assert [c.api_key for c in creds] == ["user-openai", "sk-wallet"]
