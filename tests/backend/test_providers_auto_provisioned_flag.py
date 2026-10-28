"""
@file_name: test_providers_auto_provisioned_flag.py
@author:
@date: 2026-09-03
@description: GET /api/providers marks the cards LOGIN created on the user's
    behalf, so the first-run gate can discount them without carrying its own
    copy of "which sources are auto-provisioned" (PR #383 review I7 — that
    copy silently misclassified every new account the day the backend added a
    card it did not know about).
"""
from __future__ import annotations

from backend.routes.providers import _config_to_response
from narranexus.platform.schema.provider_schema import LLMConfig, ProviderConfig


def _prov(pid: str, source: str) -> ProviderConfig:
    return ProviderConfig(
        provider_id=pid, name=pid, source=source, protocol="anthropic",
        auth_type="api_key", api_key="sk-1234567890",
    )


def test_auto_provisioned_flag_follows_the_provisioners_sources():
    cfg = LLMConfig(providers={
        "p_free": _prov("p_free", "netmind_free"),
        "p_nm": _prov("p_nm", "netmind"),
        "p_own": _prov("p_own", "user"),
        "p_cli": _prov("p_cli", "codex_oauth"),
    })
    out = _config_to_response(cfg)["providers"]
    assert out["p_free"]["auto_provisioned"] is True
    assert out["p_nm"]["auto_provisioned"] is True
    assert out["p_own"]["auto_provisioned"] is False
    assert out["p_cli"]["auto_provisioned"] is False
    # the key itself never leaves the server
    assert "api_key" not in out["p_own"]
