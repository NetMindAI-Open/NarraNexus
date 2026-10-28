"""
@file_name: test_matrix_classifier_and_cache.py
@date: 2026-07-02
@description: MatrixTrigger — room-state cache updates + DM/mention/silent
classification.

Locks the routing decisions that drive whether a message goes through
the full agent-loop path or the silent-batch memory-only path. A drift
here would either:
- silently escalate every group non-@ message to a full agent run
  (Lark-style cost regression), or
- silently drop the memory writes for group non-@ (Slack-style gap).

These are two of the exact behaviours Commit 4a/4b was built to avoid.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage


HOMESERVER = "matrix.netmind.chat"
OWNER_ID = "@owner:matrix.netmind.chat"
AGENT_ID = f"@agent-abc:{HOMESERVER}"
STRANGER_ID = "@bob:matrix.netmind.chat"


def _cred(**overrides) -> NarramessengerCredential:
    base = dict(
        agent_id="agent_x",
        bearer_token="tok",
        matrix_homeserver_url=f"https://{HOMESERVER}",
        matrix_user_id=AGENT_ID,
    )
    base.update(overrides)
    return NarramessengerCredential(**base)


def _msg(
    *,
    body: str,
    sender: str = STRANGER_ID,
    chat_id: str = "!room:h",
    mentions=None,
) -> ParsedMessage:
    # Fake nio event object with a .source dict — enough for classifier
    # to walk m.mentions.user_ids.
    source = {"content": {}}
    if mentions is not None:
        source["content"]["m.mentions"] = {"user_ids": list(mentions)}
    nio_event = SimpleNamespace(source=source)
    return ParsedMessage(
        message_id="$evt1",
        chat_id=chat_id,
        sender_id=sender,
        sender_name=sender,
        content=body,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw={"kind": "m.room.message.text", "_nio_event": nio_event},
    )


def _member_event(
    *,
    state_key: str,
    membership: str,
    prev_membership: str = "",
    displayname: str = "",
):
    return SimpleNamespace(
        state_key=state_key,
        sender=state_key,
        membership=membership,
        prev_membership=prev_membership,
        content={"displayname": displayname, "membership": membership},
    )


def _seed_verified_roster(t: MatrixTrigger, room: str, size: int) -> None:
    """Seed a roster that a joined_members lookup already verified, and a
    room with no ``ai.netmind.room_creation.v1`` state (404 already seen),
    so the classifier trusts both without touching the (fake) client."""
    t._room_joined[room] = {f"@member{i}:h" for i in range(size)}
    t._room_roster_authoritative.add(room)
    t._room_kind_checked.add(room)


def test_apply_member_join_adds_to_roster_and_stores_displayname():
    t = MatrixTrigger()
    room = "!room1:h"
    t._apply_member_event(
        room,
        _member_event(
            state_key=OWNER_ID, membership="join", displayname="Owner"
        ),
    )
    assert len(t._room_joined[room]) == 1
    assert t._display_name_cache[(room, OWNER_ID)] == "Owner"


def test_apply_member_leave_removes_from_roster_and_drops_name():
    t = MatrixTrigger()
    room = "!room1:h"
    # Seed a join first so leave has something to subtract.
    t._apply_member_event(
        room,
        _member_event(
            state_key=OWNER_ID, membership="join", displayname="Owner"
        ),
    )
    assert len(t._room_joined[room]) == 1
    t._apply_member_event(
        room,
        _member_event(
            state_key=OWNER_ID,
            membership="leave",
            prev_membership="join",
        ),
    )
    assert len(t._room_joined[room]) == 0
    assert (room, OWNER_ID) not in t._display_name_cache


def test_apply_member_re_join_is_idempotent_on_count():
    """A duplicate join (no prev_membership='join') counts once."""
    t = MatrixTrigger()
    room = "!room1:h"
    ev = _member_event(state_key=OWNER_ID, membership="join", displayname="Owner")
    t._apply_member_event(room, ev)
    # Replaying the SAME event (state block + timeline, or a since-token
    # rewind) must not inflate the room shape: the roster is a set keyed
    # by MXID. The old running counter counted this twice, which is how
    # a real 1:1 room could stop reading as a DM.
    t._apply_member_event(room, ev)
    assert t._room_joined[room] == {OWNER_ID}


@pytest.mark.asyncio
async def test_classify_dm_when_member_count_two():
    t = MatrixTrigger()
    room = "!room:h"
    _seed_verified_roster(t, room, 2)
    fake_client = SimpleNamespace()  # not touched: roster already verified
    target = await t._classify(
        fake_client, _msg(body="hi", chat_id=room), _cred()
    )
    assert target == "dm"


@pytest.mark.asyncio
async def test_classify_group_mention_by_msc3952_intentional_mentions():
    t = MatrixTrigger()
    room = "!bigroom:h"
    _seed_verified_roster(t, room, 5)
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body="hey team, quick q", chat_id=room, mentions=[AGENT_ID]),
        _cred(),
    )
    assert target == "group_mention"


@pytest.mark.asyncio
async def test_classify_group_mention_by_raw_mxid_inline():
    t = MatrixTrigger()
    room = "!bigroom:h"
    _seed_verified_roster(t, room, 5)
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body=f"can {AGENT_ID} check this?", chat_id=room),
        _cred(),
    )
    assert target == "group_mention"


@pytest.mark.asyncio
async def test_classify_group_mention_by_at_displayname():
    t = MatrixTrigger()
    room = "!bigroom:h"
    _seed_verified_roster(t, room, 5)
    # Seed our own display name.
    t._display_name_cache[(room, AGENT_ID)] = "Agent Bot"
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body="@Agent Bot are you around?", chat_id=room),
        _cred(),
    )
    assert target == "group_mention"


@pytest.mark.asyncio
async def test_classify_group_silent_when_no_mention():
    t = MatrixTrigger()
    room = "!bigroom:h"
    _seed_verified_roster(t, room, 5)
    t._display_name_cache[(room, AGENT_ID)] = "Agent Bot"
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body="hey team, how are things", chat_id=room),
        _cred(),
    )
    assert target == "group_silent"


@pytest.mark.asyncio
async def test_classify_bare_word_displayname_is_not_a_mention():
    """Regression (#4): a display name appearing as a BARE word (no @)
    must NOT count as a mention. "I called the Agent" with display name
    "Agent" used to false-fire group_mention → a full agent run + reply."""
    t = MatrixTrigger()
    room = "!bigroom:h"
    _seed_verified_roster(t, room, 5)
    t._display_name_cache[(room, AGENT_ID)] = "Agent"
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body="I already called the Agent about that", chat_id=room),
        _cred(),
    )
    assert target == "group_silent"


@pytest.mark.asyncio
async def test_classify_displayname_from_other_room_is_ignored():
    """Regression (#4): the display name is looked up ROOM-SCOPED. A name
    cached for a different room must not leak in as a mention here."""
    t = MatrixTrigger()
    other_room, this_room = "!roomA:h", "!roomB:h"
    _seed_verified_roster(t, this_room, 5)
    t._display_name_cache[(other_room, AGENT_ID)] = "Agent"  # NOT this_room
    fake_client = SimpleNamespace()
    target = await t._classify(
        fake_client,
        _msg(body="@Agent are you here?", chat_id=this_room),
        _cred(),
    )
    assert target == "group_silent"


@pytest.mark.asyncio
async def test_classify_unknown_room_defaults_to_group_silent():
    """Zero member count == cache miss == unknown shape. Default
    'group_silent' so we NEVER auto-reply to a room we can't classify;
    memory writes still fire, agent stays quiet."""
    t = MatrixTrigger()
    room = "!unknown:h"

    class _FakeClient:
        async def joined_members(self, room_id):
            # Simulate lookup failure — no members returned.
            return SimpleNamespace(status_code="M_UNKNOWN")

    target = await t._classify(_FakeClient(), _msg(body="hi", chat_id=room), _cred())
    assert target == "group_silent"
