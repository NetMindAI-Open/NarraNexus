"""
@file_name: test_netmind_provisioner.py
@author: NarraNexus
@date: 2026-07-10
@description: Unit tests for netmind_provisioner.ensure_netmind_provider.

Covers the register-vs-activate split, the flag/token/dedup short-circuits, the
inference-base forwarding, and orphan-key cleanup on onboard failure. All upstream
surfaces (key client, DB, provider service, config-complete check) are stubbed so
no network / DB is touched.
"""
from __future__ import annotations

import pytest

import xyz_agent_context.services.netmind_key_client as key_mod
import xyz_agent_context.utils.db_factory as db_factory
import xyz_agent_context.agent_framework.user_provider_service as ups_mod
import xyz_agent_context.agent_framework.provider_resolver as resolver_mod
from xyz_agent_context.services.netmind_key_client import (
    KeyAuthError,
    KeyUpstreamError,
    MintedKey,
)
from xyz_agent_context.services.netmind_provisioner import ensure_netmind_provider
from xyz_agent_context.settings import settings

USER = "user_test"
TOKEN = "jwt-abc"


def _setup(
    monkeypatch,
    *,
    enabled=True,
    existing=False,
    complete=False,
    create_raises=None,
    onboard_raises=None,
):
    """Wire fakes for every dependency and return a capture dict."""
    cap: dict = {"deleted": [], "created": None, "onboard": None}

    monkeypatch.setattr(settings, "netmind_use_subscription_enabled", enabled, raising=False)
    monkeypatch.setattr(settings, "netmind_key_api_base", "https://key.example", raising=False)
    monkeypatch.setattr(
        settings, "netmind_inference_base",
        "https://test.api.netmind.ai/inference-api", raising=False,
    )

    class _FakeKeyClient:
        def __init__(self, *a, **k):
            pass

        async def create_key(self, token):
            cap["created"] = token
            if create_raises is not None:
                raise create_raises
            return MintedKey(apitoken="mint-x", token_id=7)

        async def delete_key(self, token, tid):
            cap["deleted"].append((token, tid))

    monkeypatch.setattr(key_mod, "NetmindKeyClient", _FakeKeyClient)

    class _DB:
        async def get_one(self, table, filters):
            cap["dedup_filters"] = filters
            return {"provider_id": "p1"} if existing else None

    async def _get_db_client():
        return _DB()

    monkeypatch.setattr(db_factory, "get_db_client", _get_db_client)

    class _Svc:
        def __init__(self, db):
            pass

        async def get_user_config(self, uid):
            return object()

        async def onboard_one_key(
            self, uid, key, provider_type=None, inference_base=None, activate=True
        ):
            cap["onboard"] = dict(
                uid=uid, key=key, provider_type=provider_type,
                inference_base=inference_base, activate=activate,
            )
            if onboard_raises is not None:
                raise onboard_raises
            return (object(), ["p1", "p2"], {})

    monkeypatch.setattr(ups_mod, "UserProviderService", _Svc)
    monkeypatch.setattr(
        resolver_mod, "_is_user_config_complete", lambda cfg: complete
    )
    return cap


@pytest.mark.asyncio
async def test_flag_off_is_noop(monkeypatch):
    cap = _setup(monkeypatch, enabled=False)
    assert await ensure_netmind_provider(USER, TOKEN) is False
    assert cap["created"] is None  # never minted


@pytest.mark.asyncio
async def test_empty_token_is_noop(monkeypatch):
    cap = _setup(monkeypatch)
    assert await ensure_netmind_provider(USER, "   ") is False
    assert cap["created"] is None


@pytest.mark.asyncio
async def test_dedup_existing_is_noop(monkeypatch):
    cap = _setup(monkeypatch, existing=True)
    assert await ensure_netmind_provider(USER, TOKEN) is False
    assert cap["created"] is None  # dedup happens BEFORE minting
    assert cap["dedup_filters"] == {"user_id": USER, "source": "netmind"}


@pytest.mark.asyncio
async def test_registers_and_activates_when_config_incomplete(monkeypatch):
    cap = _setup(monkeypatch, complete=False)
    assert await ensure_netmind_provider(USER, TOKEN) is True
    assert cap["onboard"]["activate"] is True
    assert cap["onboard"]["provider_type"] == "netmind"
    assert cap["onboard"]["inference_base"] == "https://test.api.netmind.ai/inference-api"


@pytest.mark.asyncio
async def test_registers_only_when_config_complete(monkeypatch):
    cap = _setup(monkeypatch, complete=True)
    assert await ensure_netmind_provider(USER, TOKEN) is True
    assert cap["onboard"]["activate"] is False  # respect the user's own provider


@pytest.mark.asyncio
async def test_activate_if_fresh_false_forces_register_only(monkeypatch):
    cap = _setup(monkeypatch, complete=False)
    assert await ensure_netmind_provider(USER, TOKEN, activate_if_fresh=False) is True
    assert cap["onboard"]["activate"] is False


@pytest.mark.asyncio
async def test_bearer_prefix_is_stripped(monkeypatch):
    cap = _setup(monkeypatch)
    await ensure_netmind_provider(USER, "Bearer  jwt-xyz")
    assert cap["created"] == "jwt-xyz"


@pytest.mark.asyncio
async def test_key_auth_error_propagates_without_minting_onboard(monkeypatch):
    cap = _setup(monkeypatch, create_raises=KeyAuthError("bad token"))
    with pytest.raises(KeyAuthError):
        await ensure_netmind_provider(USER, TOKEN)
    assert cap["onboard"] is None
    assert cap["deleted"] == []  # nothing minted → nothing to revoke


@pytest.mark.asyncio
async def test_key_upstream_error_propagates(monkeypatch):
    _setup(monkeypatch, create_raises=KeyUpstreamError("500"))
    with pytest.raises(KeyUpstreamError):
        await ensure_netmind_provider(USER, TOKEN)


@pytest.mark.asyncio
async def test_orphan_key_revoked_on_onboard_failure(monkeypatch):
    cap = _setup(monkeypatch, onboard_raises=ValueError("key rejected by netmind"))
    with pytest.raises(ValueError):
        await ensure_netmind_provider(USER, TOKEN)
    # minted key must be revoked so no money-spending orphan lingers
    assert cap["deleted"] == [(TOKEN, 7)]
