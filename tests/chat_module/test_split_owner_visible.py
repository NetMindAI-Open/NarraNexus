"""
@file_name: test_split_owner_visible.py
@date: 2026-08-04
@description: The user-visible split consumes the OWNER-VISIBLE tool list,
not the delivered-to-origin list.

A bus turn that delivered its result to a peer agent produced nothing the
owner can see: persisting it as a normal conversation pair would surface
an agent-to-agent exchange in the owner's chat history. The splitter must
return empty for such turns (the caller then writes the background
activity row, exactly as before PR #230), while IM-channel turns keep
mirroring their channel replies (the IM conversation IS with the owner).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import xyz_agent_context.message_bus  # noqa: F401 — registers the bus handler
from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.schema import ProgressMessage
from xyz_agent_context.schema.runtime_message import ProgressStatus


def _module() -> ChatModule:
    return ChatModule(agent_id="agent_a", user_id=None, database_client=MagicMock())


def _tool_progress(tool_name: str, content: str) -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": {"content": content}},
    )


def test_bus_only_delivery_is_not_owner_visible():
    im, direct, combined = _module()._split_user_visible_response(
        [_tool_progress("mcp__message_bus_module__bus_send_to_agent", "peer reply")],
        "message_bus",
    )
    assert (im, direct, combined) == ("", "", "")


def test_owner_relay_on_bus_turn_stays_visible():
    im, direct, combined = _module()._split_user_visible_response(
        [
            _tool_progress("mcp__message_bus_module__bus_send_message", "to peers"),
            _tool_progress(
                "mcp__chat_module__send_message_to_user_directly", "for owner"
            ),
        ],
        "message_bus",
    )
    assert im == ""
    assert direct == "for owner"
    assert combined == "for owner"


def test_im_channel_reply_still_mirrors_to_owner():
    """IM handlers keep the None-fallback: their channel tools stay
    owner-visible, so the split still yields a non-empty im part (wechat's
    custom extractor returns its placeholder marker, unchanged)."""
    im, direct, combined = _module()._split_user_visible_response(
        [_tool_progress("mcp__wechat_module__wechat_send", "wechat reply")],
        "wechat",
    )
    assert im  # non-empty → the turn persists as a conversation pair
    assert combined == im
