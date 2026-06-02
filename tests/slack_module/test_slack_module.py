"""
@file_name: test_slack_module.py
@date: 2026-05-08
@description: Tests for SlackModule — config metadata, prompt branching,
extra_data shape, and registration in MODULE_MAP.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module import MODULE_MAP
from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredential,
)
from xyz_agent_context.module.slack_module.slack_module import SlackModule
from xyz_agent_context.schema import ContextData, ModuleConfig


def _ctx(extra: dict | None = None) -> ContextData:
    return ContextData(
        agent_id="agent_a",
        input_content="hi",
        extra_data=extra or {},
    )


def _make_module(db=None) -> SlackModule:
    """Construct a SlackModule with the minimum required args for unit tests."""
    return SlackModule(agent_id="agent_a", user_id=None, database_client=db)


def _cred() -> SlackCredential:
    return SlackCredential(
        agent_id="agent_a",
        bot_token="xoxb-test",
        app_token="xapp-test",
        bot_user_id="U0BOT",
        team_id="T1",
        team_name="Acme Workspace",
        enabled=True,
    )


# ── Static config ──────────────────────────────────────────────────────


def test_get_config_returns_capability_module_with_priority_six():
    cfg = SlackModule.get_config()
    assert isinstance(cfg, ModuleConfig)
    assert cfg.name == "SlackModule"
    assert cfg.priority == 6
    assert cfg.module_type == "capability"
    assert cfg.enabled is True


def test_module_map_registers_slack_module():
    assert "SlackModule" in MODULE_MAP
    assert MODULE_MAP["SlackModule"] is SlackModule


def test_class_level_channel_metadata():
    assert SlackModule.channel_name == "slack"
    assert SlackModule.brand_display == "Slack"
    assert SlackModule.ctx_data_key == "slack_info"
    assert SlackModule.mcp_server_name == "slack_module"
    assert SlackModule.mcp_port == 7831


# ── build_extra_data ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_extra_data_shape():
    module = _make_module()
    extra = await module.build_extra_data(_cred(), _ctx())

    # No owner registered → no trust signal can fire.
    assert extra == {
        "team_id": "T1",
        "team_name": "Acme Workspace",
        "bot_user_id": "U0BOT",
        "owner_user_id": "",
        "owner_name": "",
        "current_sender_id": "",
        "is_owner_interacting": False,
        "enabled": True,
    }


# ── get_instructions branching ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_instructions_returns_no_bot_block_when_unbound():
    module = _make_module()
    text = await module.get_instructions(_ctx(extra=None))

    # Discovery banner + iron rules appended
    assert "does NOT yet have a Slack bot bound" in text
    assert "slack_bind" in text
    assert "Iron rules" in text


@pytest.mark.asyncio
async def test_no_bot_block_clarifies_manifest_name_fields_are_editable():
    """Reported 2026-05-22: when binding from scratch, the user sees a
    YAML manifest with a hard-coded ``name: NarraNexus Agent`` and the
    surrounding prompt said "paste this verbatim". They didn't realise
    the name was a placeholder, OR which of the two name fields
    (app name vs bot display name) corresponded to what they'd see in
    Slack. The prompt now spells out both fields and tells the agent
    to surface that they're editable."""
    module = _make_module()
    text = await module.get_instructions(_ctx(extra=None))

    # "verbatim" wording removed — it was the source of the confusion
    assert "verbatim" not in text

    # Both name fields named so the agent can disambiguate for the user
    assert "display_information.name" in text
    assert "features.bot_user.display_name" in text

    # Editability surfaced — accept either "rename" or "edit"
    # wording to keep the assertion non-brittle
    lower = text.lower()
    assert "rename" in lower or "editable" in lower or "edit" in lower


@pytest.mark.asyncio
async def test_get_instructions_returns_full_block_when_slack_info_present():
    module = _make_module()
    ctx = _ctx(
        extra={
            "slack_info": {
                "team_id": "T1",
                "team_name": "Acme Workspace",
                "bot_user_id": "U0BOT",
                "enabled": True,
            }
        }
    )
    text = await module.get_instructions(ctx)

    assert "Acme Workspace" in text
    assert "U0BOT" in text
    assert "slack_cli" in text
    assert "slack_skill" in text
    # Iron rules appended even on the operational branch
    assert "Iron rules" in text
    # Discovery banner must NOT appear
    assert "does NOT yet have a Slack bot bound" not in text


@pytest.mark.asyncio
async def test_get_instructions_uses_unknown_workspace_when_team_name_missing():
    module = _make_module()
    ctx = _ctx(extra={"slack_info": {"bot_user_id": "U0BOT"}})
    text = await module.get_instructions(ctx)
    assert "(unknown workspace)" in text


@pytest.mark.asyncio
async def test_iron_rules_enforce_at_mention_only_in_channels():
    """Phase 5: prompt must explicitly tell the agent that in channels/
    groups it replies only when @-mentioned, and that DMs are exempt.
    The L2 trigger filter is the load-bearing defence, but the prompt
    rule is the L1 backstop and is the one users notice when behaviour
    drifts."""
    module = _make_module()
    text = await module.get_instructions(_ctx(extra=None))

    # Core directive
    assert "reply ONLY when @-mentioned" in text
    # Mechanism explanation so the agent doesn't try to compensate
    assert "app_mention" in text
    # Carve-out so it doesn't go silent in DMs
    assert "DMs are different" in text
    # Guardrail against historical-message replies
    assert "conversations.history" in text


# ── send_to_agent error path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_to_agent_returns_error_when_no_credential():
    # No DB hooked up → get_credential returns None
    module = _make_module(db=None)
    out = await module.send_to_agent("agent_a", "C1", "hi")
    assert out["success"] is False
    assert "no Slack credential" in out["error"]
