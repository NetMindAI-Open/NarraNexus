"""
@file_name: test_free_tier_provider_card.py
@description: The free-tier card is built exactly like a NetMind card.

Everything downstream (slot binding, model switching, the cloud policy) works
only because the card is ORDINARY. These tests pin the three places that had to
learn about it, so a future edit cannot quietly make it special again.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.agent_framework.providers import user_service as us
from xyz_agent_context.agent_framework.providers.cloud_policy import (
    CLOUD_BINDABLE_SOURCES,
    CloudPolicyViolation,
    ensure_slot_provider_allowed,
)
from xyz_agent_context.agent_framework.providers.free_tier import (
    FREE_TIER_SOURCE,
    free_tier_endpoints,
    is_free_tier_enabled,
)
from xyz_agent_context.agent_framework.providers.model_catalog import (
    get_default_agent_model,
    get_default_helper_model,
)


def test_endpoints_come_from_the_environment(monkeypatch):
    """The gateway URL differs per deployment (and does not exist locally), so
    it must never be a module-level constant."""
    monkeypatch.setenv("FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL", "http://gw:4000")
    monkeypatch.setenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", "http://gw:4000/v1")
    ep = free_tier_endpoints()
    assert ep.for_protocol("anthropic") == "http://gw:4000"
    assert ep.for_protocol("openai") == "http://gw:4000/v1"


def test_the_two_protocols_get_different_bases(monkeypatch):
    """The Claude CLI appends /v1/messages to its base while the OpenAI client
    expects the base to already end in /v1 — one shared URL breaks one of them."""
    monkeypatch.delenv("FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", raising=False)
    ep = free_tier_endpoints()
    assert ep.anthropic_base_url != ep.openai_base_url
    assert ep.openai_base_url.endswith("/v1")


def test_card_builds_two_linked_rows_pointing_at_the_gateway(monkeypatch):
    monkeypatch.setenv("FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL", "http://gw:4000")
    monkeypatch.setenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", "http://gw:4000/v1")

    rows = us._build_dual_providers(FREE_TIER_SOURCE, "sk-wallet", "grp1")

    assert len(rows) == 2
    assert {r["protocol"] for r in rows} == {"anthropic", "openai"}
    # One linked group = one card in the UI, two protocol rows underneath.
    assert {r["linked_group"] for r in rows} == {"grp1"}
    assert {r["source"] for r in rows} == {FREE_TIER_SOURCE}
    assert {r["api_key"] for r in rows} == {"sk-wallet"}
    by_proto = {r["protocol"]: r for r in rows}
    assert by_proto["anthropic"]["base_url"] == "http://gw:4000"
    assert by_proto["openai"]["base_url"] == "http://gw:4000/v1"


def test_card_ships_a_real_model_list_so_the_dropdown_is_not_empty(monkeypatch):
    """The card inherits NetMind's catalogue — the gateway proxies the same
    upstream, so a second list would only drift."""
    monkeypatch.setenv("FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL", "http://gw:4000")
    monkeypatch.setenv("FREE_TIER_GATEWAY_OPENAI_BASE_URL", "http://gw:4000/v1")

    rows = us._build_dual_providers(FREE_TIER_SOURCE, "sk-wallet", "grp1")
    for row in rows:
        assert json.loads(row["models"]), f"{row['protocol']} row has no models"


def test_onboarding_defaults_exist_for_the_card():
    assert get_default_agent_model(FREE_TIER_SOURCE)
    assert get_default_helper_model(FREE_TIER_SOURCE)


def test_cloud_policy_lets_the_free_card_drive_a_slot():
    """Cloud non-staff may only bind NetMind capacity. The free wallet IS
    NetMind capacity (through our gateway), so it must be bindable — otherwise
    the free tier can be registered but never used."""
    assert FREE_TIER_SOURCE in CLOUD_BINDABLE_SOURCES
    assert "netmind" in CLOUD_BINDABLE_SOURCES


def test_cloud_policy_still_rejects_a_byok_card(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.providers.cloud_policy.is_cloud_mode",
        lambda: True,
    )
    ensure_slot_provider_allowed({"source": FREE_TIER_SOURCE}, False)
    with pytest.raises(CloudPolicyViolation):
        ensure_slot_provider_allowed({"source": "user"}, False)


def test_free_tier_is_off_in_local_mode(monkeypatch):
    """The wallet lives on a gateway that only exists in the cloud, so a
    desktop install must never try to provision one."""
    monkeypatch.setenv("FREE_TIER_ENABLED", "true")
    monkeypatch.setattr(
        "xyz_agent_context.utils.deployment_mode.is_cloud_mode", lambda: False
    )
    assert is_free_tier_enabled() is False


# ── the provider driver ─────────────────────────────────────────────────────
# A card the resolver accepts but no Driver can build is a run-time 500 with a
# config that looks perfectly valid — which is exactly how this was found on
# dev. These two pin the whole chain: source → driver_type → registered class.

def test_source_derives_a_driver_type():
    from xyz_agent_context.agent_framework.providers.driver.derive import (
        derive_driver_type,
    )

    assert derive_driver_type(FREE_TIER_SOURCE, "bearer_token", "anthropic") == (
        FREE_TIER_SOURCE
    )
    assert derive_driver_type(FREE_TIER_SOURCE, "api_key", "openai") == (
        FREE_TIER_SOURCE
    )


def test_driver_type_resolves_to_a_registered_driver():
    import xyz_agent_context.agent_framework.providers.driver  # noqa: F401 — registers
    from xyz_agent_context.agent_framework.providers.driver.drivers.netmind import (
        NetMindDriver,
    )
    from xyz_agent_context.agent_framework.providers.driver.registry import (
        get_driver_class,
    )

    cls = get_driver_class(FREE_TIER_SOURCE)
    assert cls is not None, "free-tier card has no Driver — runs would 500"
    # It IS a NetMind card (ours, through the gateway), so it must inherit that
    # behaviour rather than reimplement it.
    assert issubclass(cls, NetMindDriver)


def test_every_dual_card_type_has_a_driver():
    """The guard that would have caught this class of miss up front."""
    import xyz_agent_context.agent_framework.providers.driver  # noqa: F401
    from xyz_agent_context.agent_framework.providers.driver.derive import (
        derive_driver_type,
    )
    from xyz_agent_context.agent_framework.providers.driver.registry import (
        get_driver_class,
    )

    for card_type in us._DUAL_PROVIDER_CONFIGS:
        dt = derive_driver_type(card_type, "api_key", "openai")
        assert dt, f"{card_type} derives no driver_type"
        assert get_driver_class(dt) is not None, f"{card_type} has no Driver"


def test_build_dual_providers_accepts_per_protocol_model_dict():
    # The free-tier gate hands per-protocol lists (the gateway's openai and
    # anthropic sets genuinely differ); each card row must get its own.
    import json

    from xyz_agent_context.agent_framework.providers.user_service import (
        _build_dual_providers,
    )

    rows = _build_dual_providers(
        "netmind_free", "key", "grp",
        models={"openai": ["a", "b"], "anthropic": ["a"]},
    )
    by_proto = {r["protocol"]: json.loads(r["models"]) for r in rows}
    assert by_proto["openai"] == ["a", "b"]
    assert by_proto["anthropic"] == ["a"]
