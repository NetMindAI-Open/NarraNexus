"""
@file_name: test_bus_attachment_roundtrip.py
@date: 2026-07-20
@description: Attachments survive a bus send→read round-trip (JSON column) and
surface as Read-tool markers in both prompt builders (DM/owner-relay + team).
"""

from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage

ATTS = [
    {
        "file_id": "att_1234abcd",
        "original_name": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12,
        "category": "document",
        "rel_path": "user_a/_shared/bus_files/2026-07-20/att_1234abcd.pdf",
    }
]


async def _seed(db, agent_id, owner):
    await db.insert("agents", {"agent_id": agent_id, "agent_name": agent_id, "created_by": owner})


@pytest.mark.asyncio
async def test_send_read_roundtrip_and_msg_type(db_client):
    await _seed(db_client, "agent_a", "user_a")
    await _seed(db_client, "agent_a2", "user_a")
    bus = LocalMessageBus(db_client._backend)

    msg_id = await bus.send_to_agent(
        from_agent="agent_a",
        to_agent="agent_a2",
        content="here is the report",
        attachments=ATTS,
    )
    assert msg_id

    # Fetch via the DM channel the send auto-created.
    channel = await db_client.get_one("bus_channels", {"created_by": "agent_a"})
    msgs = await bus.get_messages(channel["channel_id"])
    assert len(msgs) == 1
    got = msgs[0]
    assert got.attachments == ATTS
    assert got.msg_type == "multimodal"  # auto-tagged when files present


@pytest.mark.asyncio
async def test_text_only_stays_text(db_client):
    await _seed(db_client, "agent_a", "user_a")
    await _seed(db_client, "agent_a2", "user_a")
    bus = LocalMessageBus(db_client._backend)
    await bus.send_to_agent(from_agent="agent_a", to_agent="agent_a2", content="ping")
    channel = await db_client.get_one("bus_channels", {"created_by": "agent_a"})
    msgs = await bus.get_messages(channel["channel_id"])
    assert msgs[0].msg_type == "text"
    assert msgs[0].attachments is None


@pytest.mark.asyncio
async def test_get_recent_messages_returns_newest_in_chat_order(db_client):
    await _seed(db_client, "agent_a", "user_a")
    await _seed(db_client, "agent_a2", "user_a")
    bus = LocalMessageBus(db_client._backend)
    await bus.send_to_agent(from_agent="agent_a", to_agent="agent_a2", content="ping")
    channel = await db_client.get_one("bus_channels", {"created_by": "agent_a"})
    cid = channel["channel_id"]
    for i in range(5):
        await bus.send_message(from_agent="agent_a", to_channel=cid, content=f"m{i}")

    recent = await bus.get_recent_messages(cid, limit=3)
    # newest 3, oldest→newest chat order
    assert [m.content for m in recent] == ["m2", "m3", "m4"]


def _msg(**kw):
    base = dict(
        message_id="msg_1",
        channel_id="ch_1",
        from_agent="agent_a",
        content="see attached",
        msg_type="multimodal",
        mentions=None,
        attachments=ATTS,
        created_at="2026-07-20T00:00:00Z",
    )
    base.update(kw)
    return BusMessage(**base)


def test_owner_relay_prompt_injects_marker():
    trig = MessageBusTrigger(bus=None)
    prompt = trig._build_prompt([_msg()], owner_user_id="user_a", owner_name="Bin")
    assert "use Read tool" in prompt
    assert "report.pdf" in prompt


def test_team_prompt_injects_marker_and_shared_folder():
    trig = MessageBusTrigger(bus=None)
    member_map = {"agent_a": "Alice", "agent_b": "Bob"}
    prompt = trig._build_team_prompt(
        "agent_b",
        [_msg()],
        member_map,
        owner_user_id="user_a",
        team_id="team_42",
    )
    assert "use Read tool" in prompt  # attachment marker
    assert "Team shared folder" in prompt  # collaboration hint
    assert "teams/team_42" in prompt


def test_team_prompt_shows_history_from_others_and_points_at_trigger():
    # The recipient sees a file posted by SOMEONE ELSE (a message that did not
    # @mention it), so it can Read + discuss without a manual relay; and it's
    # pointed at the message it must answer.
    trig = MessageBusTrigger(bus=None)
    member_map = {"agent_a": "Alice", "agent_b": "Bob"}
    user_img = _msg(from_agent="usr_yzhou", content="whose place is this?", mentions=None)
    ask = _msg(from_agent="agent_a", content="@Bob take a look", msg_type="text",
               attachments=None, mentions=["agent_b"])
    prompt = trig._build_team_prompt(
        "agent_b", [user_img, ask], member_map,
        owner_user_id="user_a", team_id="team_42", trigger_messages=[ask],
    )
    # The image (from the user, NOT @Bob) is visible to Bob in the scrollback.
    assert "use Read tool" in prompt
    assert "report.pdf" in prompt
    # And Bob is told to respond to Alice's @mention.
    assert "just @mentioned by Alice" in prompt


def test_team_prompt_allows_action_tools_forbids_reply_delivery():
    # Action tools (Read + bus_share_to_team) are allowed; REPLY-DELIVERY
    # functions are forbidden (the reply auto-posts). The old blanket "no tools"
    # / "no send/bus" ban made agents refuse to open a file and fake a forward.
    trig = MessageBusTrigger(bus=None)
    for atts, mtype in [(ATTS, "multimodal"), (None, "text")]:
        prompt = trig._build_team_prompt(
            "agent_b",
            [_msg(attachments=atts, msg_type=mtype, content="see the path")],
            {"agent_a": "Alice", "agent_b": "Bob"},
        )
        assert "Do NOT use any tools" not in prompt          # no blanket ban
        assert "built-in Read tool" in prompt                # Read allowed
        assert "bus_share_to_team" in prompt                 # publishing a file allowed
        assert "send_message_to_user_directly" in prompt     # reply-delivery forbidden
