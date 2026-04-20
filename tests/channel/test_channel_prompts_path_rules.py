"""
@file_name: test_channel_prompts_path_rules.py
@author: Bin Liang
@date: 2026-04-20
@description: Bug 23 — IM channel prompts must tell the agent not to
reply with raw local file paths.

The channel recipient (Lark / Matrix / Telegram sender) is reading the
reply inside their IM client. They have no access to the agent's
filesystem. A reply that ends with "I saved it to /app/workspace/x.md"
is a dead end — the user sees a path they can never open.

The prompt fix adds a "File & Path Rules for IM Delivery" section to
``CHANNEL_MESSAGE_EXECUTION_TEMPLATE`` that:
  * names the unreachable paths explicitly (/app, ~/, /tmp, skills/)
  * gives three concrete delivery routes (inline, Lark doc URL, file upload)
  * explicitly bans "I saved it to <path>" phrasing

These tests pin the contract as substring checks — future refactors
can change wording but must preserve the three guarantees.
"""
from __future__ import annotations

from xyz_agent_context.channel.channel_prompts import (
    CHANNEL_MESSAGE_EXECUTION_TEMPLATE,
)


def _rendered_for_lark() -> str:
    """Fill the template with Lark placeholders so we can inspect the
    final prompt the agent would see."""
    return CHANNEL_MESSAGE_EXECUTION_TEMPLATE.format(
        channel_display_name="Lark",
        channel_key="lark",
        room_name="test-room",
        room_id="r1",
        room_type="Group Room",
        sender_display_name="Alice",
        sender_id="u1",
        timestamp="2026-04-20T10:00:00",
        my_channel_id="bot1",
        sender_profile_section="",
        conversation_history_section="",
        message_body="hi",
        room_members_section="",
        reply_instruction="call lark_cli(...)",
    )


def test_template_names_unreachable_paths_explicitly():
    """The prompt must call out concrete path shapes the recipient
    cannot reach — so the LLM recognises them in its own output before
    sending."""
    rendered = _rendered_for_lark().lower()
    # At least one container-style and one home-style path.
    assert "/app" in rendered or "/opt" in rendered
    assert "~/documents" in rendered or "~/" in rendered
    # Skills workspace is a common foot-gun — must be named.
    assert "skills/" in rendered


def test_template_offers_three_delivery_routes():
    """Agent needs a concrete decision tree: inline vs doc link vs
    file upload. All three should be present."""
    rendered = _rendered_for_lark().lower()
    assert "inline" in rendered
    assert "url" in rendered
    assert "upload" in rendered or "file api" in rendered


def test_template_bans_raw_path_reply_pattern():
    """The specific 'I saved it to <path>' pattern must be explicitly
    named as forbidden — this is the most common regression."""
    rendered = _rendered_for_lark().lower()
    assert "saved" in rendered and "path" in rendered
    assert "never" in rendered or "do not" in rendered or "don't" in rendered
