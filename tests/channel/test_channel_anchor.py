"""
@file_name: test_channel_anchor.py
@author: Bin Liang
@date: 2026-06-01
@description: Channel retrieval-anchor builder — strips the execution template
down to "[From <name>] <this-turn body>" for narrative embedding.

The narrative query must embed a clean anchor (sender name + this-turn body),
NOT the full 6-section execution prompt (history/profile/members/instructions),
which diluted the retrieval vector in prod.
"""
from __future__ import annotations

from xyz_agent_context.channel.channel_context_builder_base import build_channel_anchor


def test_channel_anchor_format():
    assert build_channel_anchor("张三", "明天开会吗") == "[From 张三] 明天开会吗"


def test_channel_anchor_empty_body_is_placeholder():
    assert build_channel_anchor("张三", "") == "[From 张三] (non-text message)"


def test_channel_anchor_unknown_sender():
    assert build_channel_anchor("", "hi") == "[From Unknown] hi"
    assert build_channel_anchor(None, "hi") == "[From Unknown] hi"


def test_channel_anchor_carries_no_template_noise():
    a = build_channel_anchor("Bob", "line1\nline2")
    assert a == "[From Bob] line1\nline2"
    assert "## Conversation History" not in a
    assert "## Members" not in a
