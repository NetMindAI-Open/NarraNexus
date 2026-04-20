"""
@file_name: test_inbox_write.py
@author: Bin Liang
@date: 2026-04-19
@description: Tests for LarkTrigger._write_to_inbox content fidelity.

Regression test for Bug 10: the outbound `bus_messages` row used to hard-code
"(Replied on Lark)" as its content, even though the full agent reply was
already passed in as `agent_response`. Users browsing the Inbox saw a
placeholder instead of the real message. This test locks in that the real
reply is persisted verbatim.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.lark_module import lark_trigger as lark_trigger_mod
from xyz_agent_context.module.lark_module._lark_credential_manager import LarkCredential
from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


def _make_cred(agent_id: str = "agent_test") -> LarkCredential:
    return LarkCredential(
        agent_id=agent_id,
        app_id="cli_test_app",
        app_secret_ref="appsecret:cli_test_app",
        brand="lark",
        profile_name=f"agent_{agent_id}",
    )


@pytest.mark.asyncio
async def test_outbound_row_stores_real_reply_not_placeholder(
    db_client, monkeypatch
):
    """_write_to_inbox must persist the actual agent reply, not a stub."""

    async def _get_db():
        return db_client

    monkeypatch.setattr(lark_trigger_mod, "get_db_client", _get_db)

    trigger = LarkTrigger()
    real_reply = "Here is a multi-line reply\nwith **formatting** preserved."

    await trigger._write_to_inbox(
        cred=_make_cred(),
        sender_name="Alice",
        sender_id="ou_alice",
        original_message="hi bot",
        agent_response=real_reply,
        chat_id="oc_test_chat",
    )

    channel_id = "lark_oc_test_chat"
    rows = await db_client.get(
        "bus_messages",
        {"channel_id": channel_id},
    )
    outbound = [
        r for r in rows if r.get("message_id", "").startswith("lark_out_")
    ]
    assert len(outbound) == 1, f"expected 1 outbound row, got {outbound!r}"
    assert outbound[0]["content"] == real_reply
    assert outbound[0]["content"] != "(Replied on Lark)"


@pytest.mark.asyncio
async def test_inbound_row_still_stores_original_message(
    db_client, monkeypatch
):
    """Sanity check: the inbound user message row is unaffected by the fix."""

    async def _get_db():
        return db_client

    monkeypatch.setattr(lark_trigger_mod, "get_db_client", _get_db)

    trigger = LarkTrigger()

    await trigger._write_to_inbox(
        cred=_make_cred(),
        sender_name="Alice",
        sender_id="ou_alice",
        original_message="hi bot",
        agent_response="noted.",
        chat_id="oc_test_chat",
    )

    rows = await db_client.get(
        "bus_messages",
        {"channel_id": "lark_oc_test_chat"},
    )
    inbound = [
        r for r in rows if r.get("message_id", "").startswith("lark_in_")
    ]
    assert len(inbound) == 1
    assert inbound[0]["content"] == "hi bot"


@pytest.mark.asyncio
async def test_empty_agent_response_writes_no_outbound_row(
    db_client, monkeypatch
):
    """If the agent produced no reply, don't fabricate an outbound row."""

    async def _get_db():
        return db_client

    monkeypatch.setattr(lark_trigger_mod, "get_db_client", _get_db)

    trigger = LarkTrigger()

    await trigger._write_to_inbox(
        cred=_make_cred(),
        sender_name="Alice",
        sender_id="ou_alice",
        original_message="hi bot",
        agent_response="",
        chat_id="oc_test_chat",
    )

    rows = await db_client.get(
        "bus_messages",
        {"channel_id": "lark_oc_test_chat"},
    )
    outbound = [
        r for r in rows if r.get("message_id", "").startswith("lark_out_")
    ]
    assert outbound == []
