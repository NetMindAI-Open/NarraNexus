"""
@file_name: test_quota_route.py
@author: rujing.yan
@date: 2026-07-23
@description: Tests for the real GET /api/quota/me handler.

The free tier is a USD wallet on the LLM gateway, so this route is a
read-through to the deploy-side wallet service. What matters here is that each
distinguishable state gets its OWN response shape — the frontend switches on
them exhaustively, and collapsing "no wallet yet" into "wallet is empty" would
show a brand-new user a spent balance.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.routes.quota as mod
from xyz_agent_context.integrations.free_tier.wallet_client import (
    WalletBalance,
    WalletMissing,
    WalletUnavailable,
)


def _balance(remaining: float, spend: float, exhausted: bool = False) -> WalletBalance:
    return WalletBalance(
        currency="USD",
        max_budget=10.0,
        spend=spend,
        remaining=remaining,
        exhausted=exhausted,
    )


def _build_client(monkeypatch, *, enabled=True, client=None, user_id="u1"):
    monkeypatch.setattr(mod, "is_free_tier_enabled", lambda: enabled)
    monkeypatch.setattr(
        mod.WalletClient, "from_settings", classmethod(lambda cls: client)
    )

    app = FastAPI()
    app.include_router(mod.router)

    @app.middleware("http")
    async def _inject_user(request, call_next):
        request.state.user_id = user_id
        return await call_next(request)

    return TestClient(app)


def _wallet(**kwargs):
    m = MagicMock()
    m.balance = AsyncMock(**kwargs)
    return m


def test_disabled_free_tier_reports_off(monkeypatch):
    client = _build_client(monkeypatch, enabled=False)
    assert client.get("/api/quota/me").json() == {"enabled": False}


def test_flag_on_but_service_unwired_reports_off(monkeypatch):
    """A half-configured deployment must not 500 the settings panel — the
    misconfiguration is already logged loudly by the provisioner."""
    client = _build_client(monkeypatch, client=None)
    assert client.get("/api/quota/me").json() == {"enabled": False}


def test_active_wallet_returns_the_balance(monkeypatch):
    client = _build_client(
        monkeypatch, client=_wallet(return_value=_balance(6.2, 3.8))
    )
    assert client.get("/api/quota/me").json() == {
        "enabled": True,
        "status": "active",
        "currency": "USD",
        "max_budget": 10.0,
        "spend": 3.8,
        "remaining": 6.2,
    }


def test_spent_wallet_is_exhausted_not_missing(monkeypatch):
    client = _build_client(
        monkeypatch, client=_wallet(return_value=_balance(0.0, 10.0, exhausted=True))
    )
    body = client.get("/api/quota/me").json()
    assert body["status"] == "exhausted"
    assert body["remaining"] == 0.0


def test_no_wallet_yet_is_uninitialized_not_an_error(monkeypatch):
    """Provisioning is fire-and-forget off the login path, so a just-registered
    user can legitimately hit this before their wallet exists."""
    client = _build_client(
        monkeypatch, client=_wallet(side_effect=WalletMissing("none"))
    )
    assert client.get("/api/quota/me").json() == {
        "enabled": True,
        "status": "uninitialized",
    }


def test_service_outage_is_503_not_a_fake_zero_balance(monkeypatch):
    """Reporting $0 when we simply cannot reach the service would tell the user
    their credit is gone. Fail loudly instead."""
    client = _build_client(
        monkeypatch, client=_wallet(side_effect=WalletUnavailable("down"))
    )
    assert client.get("/api/quota/me").status_code == 503


def test_anonymous_request_is_401(monkeypatch):
    client = _build_client(
        monkeypatch, client=_wallet(return_value=_balance(6.2, 3.8)), user_id=None
    )
    assert client.get("/api/quota/me").status_code == 401
