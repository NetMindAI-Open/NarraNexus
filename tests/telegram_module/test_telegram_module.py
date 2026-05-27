"""
@file_name: test_telegram_module.py
@date: 2026-05-09
@description: Tests for TelegramModule — config metadata, prompt
branching across owner trust states, extra_data shape, and registration
in MODULE_MAP.

Why this file exists:
    The module is the surface the orchestrator + frontend talk to. Its
    contract: priority=7 capability, ctx_data_key=telegram_info,
    register-once in MODULE_MAP. The instructions branch off three
    owner-trust states (no owner / owner match / owner mismatch) which
    must each render distinct guidance to the agent.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module import MODULE_MAP
from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_module import TelegramModule
from xyz_agent_context.schema import ContextData, ModuleConfig


def _ctx(extra: dict | None = None) -> ContextData:
    return ContextData(
        agent_id="agent_a",
        input_content="hi",
        extra_data=extra or {},
    )


def _make_module(db=None) -> TelegramModule:
    return TelegramModule(agent_id="agent_a", user_id=None, database_client=db)


def _cred(
    owner_user_id: str = "",
    owner_name: str = "",
    owner_username: str = "",
) -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        owner_username=owner_username,
        enabled=True,
    )


# ── Static config ──────────────────────────────────────────────────────


def test_get_config_returns_capability_module_with_priority_seven():
    cfg = TelegramModule.get_config()
    assert isinstance(cfg, ModuleConfig)
    assert cfg.name == "TelegramModule"
    assert cfg.priority == 7
    assert cfg.module_type == "capability"
    assert cfg.enabled is True


def test_module_map_registers_telegram_module():
    assert "TelegramModule" in MODULE_MAP
    assert MODULE_MAP["TelegramModule"] is TelegramModule


# ── build_extra_data ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_extra_data_no_owner_returns_no_trust_signal():
    module = _make_module()
    extra = await module.build_extra_data(_cred(), _ctx())

    assert extra["bot_user_id"] == "1001"
    assert extra["bot_username"] == "acme_bot"
    assert extra["owner_user_id"] == ""
    assert extra["is_owner_interacting"] is False
    assert extra["enabled"] is True


@pytest.mark.asyncio
async def test_build_extra_data_owner_match_sets_trust_signal():
    module = _make_module()
    cred = _cred(
        owner_user_id="555",
        owner_name="Bin Liang",
        owner_username="bin_liang",
    )
    ctx = _ctx(extra={"channel_tag": {"sender_id": "555"}})

    extra = await module.build_extra_data(cred, ctx)

    assert extra["is_owner_interacting"] is True
    assert extra["current_sender_id"] == "555"
    assert extra["owner_user_id"] == "555"


# ── get_instructions branching ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_instructions_returns_no_bot_block_when_unbound():
    module = _make_module()
    text = await module.get_instructions(_ctx(extra=None))

    # Discovery banner specific to Telegram setup
    assert "BotFather" in text
    # Privacy mode guidance — KEEP DEFAULT ON, do NOT disable.
    # Earlier draft told users to /setprivacy → Disable; the right
    # default is the opposite (matches Slack Phase 5 reply-policy work).
    # The phrase "KEEP DEFAULT ON" must be present so the agent
    # actively guides users away from disabling privacy.
    assert "KEEP DEFAULT ON" in text
    assert "DO NOT" in text  # explicit anti-disable instruction
    # Iron rules appended (note: "Iron rules" appears twice — in the
    # discovery prompt's setup section AND in the always-appended block)
    assert "Iron rules" in text


@pytest.mark.asyncio
async def test_no_bot_instruction_does_not_recommend_disable_privacy():
    """Regression: an earlier draft told users to ``/setprivacy → Disable``,
    which makes the bot receive every group message — same Slack
    Phase 5 reply-policy bug. The new prompt:
      - explicitly says KEEP DEFAULT ON (positive recommendation)
      - explicitly says DO NOT (negative-imperative against disabling)
      - establishes @-mention-only as the iron rule for groups
    Keep this test so the recommendation can't silently regress to
    the noisy-listener default."""
    module = _make_module()
    text = await module.get_instructions(_ctx(extra=None))

    # Positive recommendation present
    assert "KEEP DEFAULT ON" in text
    # Anti-disable language present (case-insensitive — appears in
    # both Step 2 body and the iron-rule section)
    lower = text.lower()
    assert lower.count("do not") >= 2  # at least Step 2 + iron rule 4
    # The string "Disable" still appears (unavoidable when explaining
    # what NOT to do), but every occurrence must be in close proximity
    # to a "DO NOT" / "do not" / "Do NOT" disclaimer.
    for hit in _find_disable_indexes(text):
        window = text[max(0, hit - 80) : hit + 30].lower()
        assert "do not" in window or "do not" in window, (
            f"'Disable' at index {hit} appears without nearby 'do not' "
            f"disclaimer — the prompt may be regressing to recommend "
            f"disabling privacy. Window: {text[max(0, hit-80):hit+30]!r}"
        )
    # Iron rule must enforce @-mention-only in groups
    assert "@-mention" in text or "@-mentioned" in text


def _find_disable_indexes(text: str) -> list[int]:
    """All start-positions of the literal token 'Disable' in ``text``."""
    out = []
    start = 0
    while True:
        i = text.find("Disable", start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


@pytest.mark.asyncio
async def test_get_instructions_returns_full_block_when_bound_no_owner():
    module = _make_module()
    ctx = _ctx(
        extra={
            "telegram_info": {
                "bot_username": "acme_bot",
                "bot_user_id": "1001",
                "owner_user_id": "",
                "owner_name": "",
                "is_owner_interacting": False,
                "current_sender_id": "42",
                "enabled": True,
            }
        }
    )
    text = await module.get_instructions(ctx)

    assert "@acme_bot" in text
    assert "1001" in text
    assert "tg_cli" in text
    assert "tg_skill" in text
    # No-owner branch wording
    assert "No owner has been registered" in text
    # Discovery banner must NOT appear when bound. (Operational template
    # has no BotFather references — that's only in the unbound discovery
    # prompt.)
    assert "no bot bound yet" not in text
    assert "BotFather" not in text


@pytest.mark.asyncio
async def test_get_instructions_owner_match_renders_trust_block():
    module = _make_module()
    ctx = _ctx(
        extra={
            "telegram_info": {
                "bot_username": "acme_bot",
                "bot_user_id": "1001",
                "owner_user_id": "555",
                "owner_name": "Bin Liang",
                "is_owner_interacting": True,
                "current_sender_id": "555",
                "enabled": True,
            }
        }
    )
    text = await module.get_instructions(ctx)

    assert "Bin Liang" in text
    assert "is_owner_interacting=True" in text
    assert "may surface owner-private context" in text


@pytest.mark.asyncio
async def test_get_instructions_owner_mismatch_renders_visitor_block():
    module = _make_module()
    ctx = _ctx(
        extra={
            "telegram_info": {
                "bot_username": "acme_bot",
                "bot_user_id": "1001",
                "owner_user_id": "555",
                "owner_name": "Bin Liang",
                "is_owner_interacting": False,
                "current_sender_id": "999",
                "enabled": True,
            }
        }
    )
    text = await module.get_instructions(ctx)

    assert "is_owner_interacting=False" in text
    assert "Treat as a visitor" in text
    assert "Never disclose owner-private context" in text
