"""
@file_name: test_framework_provider_binding.py
@author: rujing.yan
@date: 2026-07-31
@description: Subscription-credential cards bind only to their own CLI framework.

A Claude Code Login / Codex CLI Login card is a credential FOR A SPECIFIC CLI,
not a generic provider key. nexus_power drives the provider HTTP API and
refuses subscription auth outright, so binding such a card to a nexus_power
agent slot is a guaranteed run-time failure — it must be refused at config
time instead. The rule lives in ``provider_schema.framework_can_drive_provider``
and is enforced through the shared ``validate_slot_binding``, so both slot
writers (user-level and per-agent) inherit it.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.providers.user_service import (
    validate_slot_binding,
)
from xyz_agent_context.schema.provider_schema import framework_can_drive_provider


def _card(source: str, protocol: str, auth_type: str) -> dict:
    return {
        "provider_id": f"prov_{source}",
        "name": source,
        "source": source,
        "protocol": protocol,
        "auth_type": auth_type,
    }


CLAUDE_LOGIN = _card("claude_oauth", "anthropic", "oauth")
CLAUDE_SETUP_TOKEN = _card("claude_oauth", "anthropic", "oauth_token")
CODEX_LOGIN = _card("codex_oauth", "openai", "oauth")
ANTHROPIC_KEY = _card("user", "anthropic", "api_key")
NETMIND_OPENAI = _card("netmind", "openai", "bearer_token")


# ---- the predicate itself -------------------------------------------------


@pytest.mark.parametrize(
    "framework, card, expected",
    [
        # A subscription card is redeemable only by its own CLI.
        ("claude_code", CLAUDE_LOGIN, True),
        ("claude_code", CLAUDE_SETUP_TOKEN, True),
        ("nexus_power", CLAUDE_LOGIN, False),
        ("nexus_power", CLAUDE_SETUP_TOKEN, False),
        ("codex_cli", CODEX_LOGIN, True),
        ("nexus_power", CODEX_LOGIN, False),
        # Cross-CLI was already impossible on protocol; still false.
        ("codex_cli", CLAUDE_LOGIN, False),
        ("claude_code", CODEX_LOGIN, False),
        # API-key / bearer cards are untouched by the subscription gate —
        # only the framework's protocol requirement applies (rule #15).
        ("nexus_power", ANTHROPIC_KEY, True),
        ("nexus_power", NETMIND_OPENAI, True),
        ("claude_code", ANTHROPIC_KEY, True),
        ("claude_code", NETMIND_OPENAI, False),  # protocol mismatch
        ("codex_cli", NETMIND_OPENAI, True),
    ],
)
def test_framework_can_drive_provider(framework, card, expected):
    assert (
        framework_can_drive_provider(
            framework,
            source=card["source"],
            auth_type=card["auth_type"],
            protocol=card["protocol"],
        )
        is expected
    )


def test_unregistered_oauth_source_is_refused_everywhere():
    """An OAuth card type nobody registered in CLI_FRAMEWORK_BY_OAUTH_SOURCE
    binds to NO framework — the allow-list fails closed on purpose."""
    unknown = _card("future_oauth", "anthropic", "oauth")
    for framework in ("claude_code", "codex_cli", "nexus_power"):
        assert not framework_can_drive_provider(
            framework,
            source=unknown["source"],
            auth_type=unknown["auth_type"],
            protocol=unknown["protocol"],
        )


# ---- the shared slot validator -------------------------------------------


def test_agent_slot_rejects_claude_login_on_nexus_power():
    with pytest.raises(ValueError, match="CLI subscription"):
        validate_slot_binding(CLAUDE_LOGIN, "agent", "nexus_power")


def test_agent_slot_rejects_codex_login_on_nexus_power():
    with pytest.raises(ValueError, match="CLI subscription"):
        validate_slot_binding(CODEX_LOGIN, "agent", "nexus_power")


def test_agent_slot_accepts_each_login_on_its_own_framework():
    validate_slot_binding(CLAUDE_LOGIN, "agent", "claude_code")
    validate_slot_binding(CLAUDE_SETUP_TOKEN, "agent", "claude_code")
    validate_slot_binding(CODEX_LOGIN, "agent", "codex_cli")


def test_agent_slot_accepts_api_key_cards_on_nexus_power():
    validate_slot_binding(ANTHROPIC_KEY, "agent", "nexus_power")
    validate_slot_binding(NETMIND_OPENAI, "agent", "nexus_power")


def test_helper_slot_still_accepts_subscription_cards():
    """Rule 2 is AGENT-slot only: an OAuth helper runs its structured calls
    one-shot through the CLI (CliHelperSDK), so one subscription still covers
    both slots regardless of the agent framework."""
    validate_slot_binding(CLAUDE_LOGIN, "helper_llm", "nexus_power")
    validate_slot_binding(CODEX_LOGIN, "helper_llm", "nexus_power")
