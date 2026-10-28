"""
@file_name: test_lark_context_builder.py
@author: Bin Liang
@date: 2026-04-19
@description: Tests for LarkContextBuilder.get_message_info.

Locks in the reply_instruction contract: the Lark trigger tells the agent
to send via `--markdown` so Lark renders headings/bullets/line breaks,
instead of the previous `--text` default that leaked raw Markdown
characters into user-facing replies.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.lark_module.lark_context_builder import (
    LarkContextBuilder,
)


class _DummyCred:
    """Minimal LarkCredential stand-in; only fields get_message_info reads."""

    def __init__(self) -> None:
        self.brand = "lark"
        self.app_id = "cli_test_app"
        self.agent_id = "agent_test"


@pytest.fixture
def builder() -> LarkContextBuilder:
    event = {
        "chat_id": "oc_test_chat",
        "chat_type": "p2p",
        "chat_name": "Test Room",
        "sender_id": "ou_sender",
        "sender_name": "Sender",
        "content": "hi",
        "create_time": "0",
    }
    return LarkContextBuilder(
        event=event,
        credential=_DummyCred(),
        cli=None,  # get_message_info does not touch CLI
        agent_id="agent_test",
    )


@pytest.mark.asyncio
async def test_reply_instruction_uses_markdown(builder: LarkContextBuilder) -> None:
    info = await builder.get_message_info()
    assert "--markdown YOUR_REPLY" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_reply_instruction_does_not_forbid_markdown(
    builder: LarkContextBuilder,
) -> None:
    """The old prompt explicitly said 'not --markdown' — ensure it's gone."""
    info = await builder.get_message_info()
    assert "not `--markdown`" not in info["reply_instruction"]
    assert "not --markdown" not in info["reply_instruction"]


@pytest.mark.asyncio
async def test_reply_instruction_mentions_text_fallback(
    builder: LarkContextBuilder,
) -> None:
    """Agent should know `--text` is still available for exact-text cases."""
    info = await builder.get_message_info()
    assert "--text" in info["reply_instruction"]


@pytest.mark.asyncio
async def test_reply_instruction_embeds_chat_id_and_agent_id(
    builder: LarkContextBuilder,
) -> None:
    info = await builder.get_message_info()
    assert "oc_test_chat" in info["reply_instruction"]
    assert "agent_test" in info["reply_instruction"]
