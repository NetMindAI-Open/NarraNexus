"""
@file_name: test_owner_relay_human_name.py
@author: NarraNexus
@date: 2026-06-12
@description: The message-bus Owner-Relay prompt must NAME the owner by their
human display name, while the send_message_to_user_directly routing argument
keeps the raw user_id (the tool needs the real key to deliver).
"""
from __future__ import annotations

from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger


def test_owner_relay_shows_name_but_routing_keeps_user_id():
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    prompt = trigger._build_prompt(
        messages=[],
        owner_user_id="owner_hex_32",
        owner_name="Alice",
    )
    # Human-facing relay line names the owner.
    assert "Alice" in prompt
    # The hex must NOT appear as the owner's identity prose...
    assert "user_id=`owner_hex_32`" not in prompt
    # ...but the routing argument for the delivery tool keeps the real key.
    assert 'user_id="owner_hex_32"' in prompt
