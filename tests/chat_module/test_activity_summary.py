"""
@file_name: test_activity_summary.py
@author: Bin Liang
@date: 2026-07-03
@description: _build_activity_summary — the inner-thought one-line summary.

The Inner Thoughts card badges the source (working_source) with its own colour
and name, so the summary should say WHAT happened, not repeat the source. It
uses the channel_tag the IM triggers attach (sender / room) to be informative
instead of the old "Background activity (wechat)" boilerplate.
"""
from xyz_agent_context.module.chat_module.chat_module import ChatModule


def _summary(working_source, **tag):
    meta = {"channel_tag": tag} if tag else {}
    return ChatModule._build_activity_summary(working_source, meta)


def test_job_summary():
    assert _summary("job") == "Ran a scheduled job"


def test_im_summary_uses_sender_name():
    assert _summary("wechat", sender_name="大西瓜") == "Handled a message from 大西瓜"


def test_im_summary_falls_back_to_room_name():
    assert _summary("lark", room_name="Team Room") == "Handled a message from Team Room"


def test_im_summary_without_tag_is_generic():
    assert _summary("telegram") == "Handled a background activity"


def test_message_bus_summary():
    assert _summary("message_bus") == "Handled a peer-agent message"
    assert _summary("message_bus", sender_name="agent_peer") == "Replied to agent_peer"


def test_summary_never_echoes_raw_source_token():
    # The old "Background activity (wechat)" leaked the source token; the new
    # summaries must not, since the UI already shows the source badge.
    for ws in ("wechat", "lark", "slack", "discord", "job", "message_bus"):
        assert f"({ws})" not in _summary(ws)
