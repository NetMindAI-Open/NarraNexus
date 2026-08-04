"""
@file_name: test_trigger_owner_relay_prompt.py
@date: 2026-04-21
@description: Lock the contract that MessageBusTrigger's prompt includes an
              explicit "owner relay" directive when the triggered agent has a
              known owner. Without this directive, agents were treating peer
              bus exchanges as self-contained — replying to the peer (or
              staying silent) after processing, so the owner who asked
              "go talk to agent_B for me" never saw the reply in chat.
              The reply only landed in the Inbox, creating a silent-failure
              UX (observed in production, user complaint: "agent said it
              would tell me but only inbox shows anything").
"""

from datetime import datetime, timezone

from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.message_bus.message_bus_trigger import (
    MessageBusTrigger,
)


def _msg(content: str = "hello", from_agent: str = "agent_peer") -> BusMessage:
    return BusMessage(
        message_id="msg_1",
        channel_id="chan_x",
        from_agent=from_agent,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


def _trigger() -> MessageBusTrigger:
    # Constructor args aren't needed for _build_prompt — it's a pure method.
    # MessageBusTrigger.__init__ may require deps we don't want to mock, so
    # build an uninitialised instance. __init__ is only needed for polling.
    return MessageBusTrigger.__new__(MessageBusTrigger)


def test_prompt_without_owner_stays_minimal():
    """Legacy behaviour: no owner_user_id → no relay directive appended."""
    prompt = _trigger()._build_prompt([_msg()], owner_user_id="", i_started_this_exchange=True)
    assert "[Message Bus - Incoming Messages]" in prompt
    assert "Owner Relay" not in prompt
    assert "send_message_to_user_directly" not in prompt


def test_prompt_with_owner_includes_relay_directive():
    """The new contract: owner is known → prompt instructs the agent to
    relay the peer exchange back to the owner's chat."""
    prompt = _trigger()._build_prompt(
        [_msg(content="S&P closed at 7109")], owner_user_id="user_tc",
        i_started_this_exchange=True,
    )
    # Section header
    assert "Owner Relay" in prompt
    # Owner ID embedded so the agent can pass it to the tool
    assert "user_tc" in prompt
    # The actual tool name the agent must invoke
    assert "send_message_to_user_directly" in prompt
    # Peer content still present
    assert "S&P closed at 7109" in prompt


def test_relay_directive_mentions_silent_failure():
    """The directive must explain *why* silence is wrong. If the agent only
    sees 'please call X', it may still decide the call is optional. We
    include an explicit 'silent-failure' framing to push back against that."""
    prompt = _trigger()._build_prompt([_msg()], owner_user_id="user_tc", i_started_this_exchange=True)
    lower = prompt.lower()
    assert "silent" in lower or "the owner sees nothing" in lower
    # Frames the alternative (inbox) as insufficient
    assert "inbox" in lower


def test_relay_directive_covers_followup_case():
    """Two possible agent actions after seeing a peer reply:
       (a) peer answered, summarise to owner
       (b) peer needs clarification, send follow-up + status to owner
    Both paths must be in the directive so the agent doesn't get stuck in
    case (b) without telling the owner the thread is alive."""
    prompt = _trigger()._build_prompt([_msg()], owner_user_id="user_tc", i_started_this_exchange=True)
    assert "bus_send_to_agent" in prompt  # follow-up path
    assert "send_message_to_user_directly" in prompt  # both paths
    # Status-update framing for case (b)
    assert "waiting" in prompt or "status" in prompt


def test_peer_messages_preserved_above_directive():
    """The peer message must still be the first thing the agent reads —
    directive is a footer, not a header."""
    prompt = _trigger()._build_prompt(
        [_msg(content="UNIQUE_PEER_CONTENT")], owner_user_id="user_tc",
        i_started_this_exchange=True,
    )
    peer_idx = prompt.index("UNIQUE_PEER_CONTENT")
    directive_idx = prompt.index("Owner Relay")
    assert peer_idx < directive_idx
