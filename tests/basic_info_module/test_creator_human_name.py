"""
@file_name: test_creator_human_name.py
@author: NarraNexus
@date: 2026-06-11
@description: BasicInfoModule identity resolution — the agent's prompt must
name its Creator and the current speaker by HUMAN name, never the opaque
user_id (a 32-hex NetMind userSystemCode in cloud mode). Also fixes the
is_creator bug: agent_runtime overrides user_id to the owner, so is_creator
must be derived from the real sender (extra_data.sender_user_id), not the
(always-owner) self.user_id.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.schema.context_schema import ContextData
from xyz_agent_context.module.basic_info_module.basic_info_module import BasicInfoModule


async def _seed_user(db, user_id, display_name=None):
    row = {"user_id": user_id, "user_type": "individual", "status": "active"}
    if display_name:
        row["display_name"] = display_name
    await db.insert("users", row)


async def _seed_agent(db, agent_id, created_by):
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": "Aria", "created_by": created_by,
    })


def _ctx(user_id, extra=None):
    c = ContextData(agent_id="agent_x", user_id=user_id, input_content="hi")
    c.extra_data = extra or {}
    return c


@pytest.mark.asyncio
async def test_creator_rendered_as_human_name(db_client):
    await _seed_user(db_client, "owner_hex", "Alice")
    await _seed_agent(db_client, "agent_x", "owner_hex")
    mod = BasicInfoModule("agent_x", user_id="owner_hex", database_client=db_client)

    # Owner chatting with their own agent (no sender override -> falls back to owner).
    ctx = await mod.hook_data_gathering(_ctx("owner_hex"))

    assert ctx.creator_name == "Alice"
    assert ctx.creator_id == "owner_hex"        # key preserved for internal use
    assert ctx.is_creator is True
    assert ctx.current_speaker_name == "Alice"


@pytest.mark.asyncio
async def test_visitor_via_chat_sender(db_client):
    # Public-agent case: the logged-in sender is NOT the owner.
    await _seed_user(db_client, "owner_hex", "Alice")
    await _seed_user(db_client, "visitor_hex", "Bob")
    await _seed_agent(db_client, "agent_x", "owner_hex")
    # agent_runtime overrides user_id to the owner; the real sender rides in extra_data.
    mod = BasicInfoModule("agent_x", user_id="owner_hex", database_client=db_client)

    ctx = await mod.hook_data_gathering(
        _ctx("owner_hex", extra={"sender_user_id": "visitor_hex"})
    )

    assert ctx.creator_name == "Alice"           # creator is still Alice
    assert ctx.is_creator is False               # but the SENDER is a visitor
    assert ctx.user_role == "User/Customer"
    assert ctx.current_speaker_name == "Bob"     # named the visitor


@pytest.mark.asyncio
async def test_creator_name_falls_back_to_id_when_no_display_name(db_client):
    await _seed_user(db_client, "owner_hex")     # no display_name
    await _seed_agent(db_client, "agent_x", "owner_hex")
    mod = BasicInfoModule("agent_x", user_id="owner_hex", database_client=db_client)

    ctx = await mod.hook_data_gathering(_ctx("owner_hex"))

    assert ctx.creator_name == "owner_hex"       # graceful fallback


def test_template_uses_creator_name_not_creator_id():
    from xyz_agent_context.module.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
    )
    assert "{creator_name}" in BASIC_INFO_MODULE_INSTRUCTIONS
    assert "{current_speaker_name}" in BASIC_INFO_MODULE_INSTRUCTIONS
    # The raw hex placeholders must be gone from the human-facing identity lines.
    assert "{creator_id}" not in BASIC_INFO_MODULE_INSTRUCTIONS
