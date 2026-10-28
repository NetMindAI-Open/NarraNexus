"""
@file_name: test_matrix_room_shape.py
@date: 2026-10-16
@description: MatrixTrigger — room shape (joined-member roster) is
idempotent and authoritative via ``joined_members``.

Prod regression (2026-10-16): a user adds an official public agent as a
friend, NarraMessenger creates a fresh 1:1 room and invites the agent,
the agent auto-joins. The first sync after the join only carries SOME of
the room's ``m.room.member`` events (often just the agent's own join).
The old running counter cached that partial number (1), the classifier
never consulted the ``joined_members`` REST endpoint again, and every
message in the new 1:1 room was classified ``group_silent`` — the agent
never replied.

Contract locked here:
- Membership is a per-room SET of joined MXIDs; replaying an event is a
  no-op.
- A roster built only from incremental sync member events is NOT
  authoritative; the first classification of a room performs one
  ``joined_members`` lookup, after which member events keep the roster
  current without further REST calls.
- REST failure / empty roster → 0 (unknown → group_silent, fail-closed)
  and the next message retries the lookup.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock

import pytest
from nio import (
    JoinError,
    JoinResponse,
    RoomGetStateEventError,
    RoomGetStateEventResponse,
    UnknownEvent,
)

from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus_plugins.narramessenger_module import matrix_trigger as mt
from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _AuthorizeVerdict,
)
import narranexus.platform.channel.channel_trigger_base as base_mod
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage


ROOM = "!fresh:matrix.netmind.chat"
AGENT_MXID = "@agent-abc:matrix.netmind.chat"
USER_MXID = "@alice:matrix.netmind.chat"
THIRD_MXID = "@bob:matrix.netmind.chat"
# NarraMessenger's appservice bot joins every managed room (since 2026-09-16).
BOT_MXID = "@hybrid-im-bot:matrix.netmind.chat"
ROOM_CREATION = "ai.netmind.room_creation.v1"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-bearer",
        backend_base_url="https://api.netmind.chat",
        matrix_user_id=AGENT_MXID,
    )


def _member_event(
    mxid: str,
    membership: str,
    *,
    prev_membership: str = "",
    displayname: str = "",
):
    """Shape of a nio ``RoomMemberEvent`` as the trigger reads it."""
    return SimpleNamespace(
        state_key=mxid,
        sender=mxid,
        membership=membership,
        prev_membership=prev_membership,
        content={"displayname": displayname, "membership": membership},
    )


def _roster(*mxids: str):
    return [SimpleNamespace(user_id=m, display_name=m.split(":")[0][1:]) for m in mxids]


class _FakeClient:
    """nio ``AsyncClient`` stand-in with a controllable ``joined_members``.

    ``responses`` is consumed one entry per call; the last entry repeats.
    An entry is either an exception instance (raised), ``None`` (an error
    response without ``.members``), or a list of members (success).
    """

    def __init__(
        self, *responses, user_id: str = AGENT_MXID, room_type: Optional[str] = None
    ):
        self._responses = list(responses)
        self.calls: list[str] = []
        # ``ai.netmind.room_creation.v1`` as the homeserver would serve it:
        # None = the room has no such state (404 M_NOT_FOUND).
        self.room_type = room_type
        self.state_calls: list[tuple[str, str, str]] = []
        # Responses served before falling back to ``room_type`` (one per
        # call), and hooks run while a state request is in flight.
        self.state_overrides: list[Any] = []
        self.during_state: list[Optional[Callable[[], Any]]] = []
        self.user_id = user_id
        # One hook per lookup call (None = quiet), run inside the call before
        # the response is returned — models sync-loop work that lands while
        # the REST call is in flight.
        self.during_lookup: list[Optional[Callable[[], Any]]] = []

    async def joined_members(self, room_id):
        self.calls.append(room_id)
        entry = (
            self._responses.pop(0)
            if len(self._responses) > 1
            else self._responses[0]
        )
        hook = self.during_lookup.pop(0) if self.during_lookup else None
        if hook is not None:
            result = hook()
            if asyncio.iscoroutine(result):
                await result
        if isinstance(entry, BaseException):
            raise entry
        if entry is None:
            return SimpleNamespace(status_code="M_FORBIDDEN")
        return SimpleNamespace(members=list(entry))

    async def room_get_state_event(self, room_id, event_type, state_key=""):
        self.state_calls.append((room_id, event_type, state_key))
        hook = self.during_state.pop(0) if self.during_state else None
        if hook is not None:
            hook()
        if self.state_overrides:
            return self.state_overrides.pop(0)
        if self.room_type is None:
            return RoomGetStateEventError("Event not found.", "M_NOT_FOUND", room_id=room_id)
        return _state_response(
            {"message_mode": "managed", "room_type": self.room_type}, 200
        )


def _state_response(content: dict, status: int) -> RoomGetStateEventResponse:
    """What nio returns for any non-404 HTTP status: the body as content."""
    resp = RoomGetStateEventResponse(content, ROOM_CREATION, "", ROOM)
    resp.transport_response = SimpleNamespace(status=status)  # type: ignore[assignment]
    return resp


def _msg(
    body: str = "hello",
    *,
    sender: str = USER_MXID,
    mentions=None,
    chat_type: ChatType = ChatType.GROUP,
) -> ParsedMessage:
    source = {"content": {}}
    if mentions is not None:
        source["content"]["m.mentions"] = {"user_ids": list(mentions)}
    return ParsedMessage(
        message_id="$evt1",
        chat_id=ROOM,
        sender_id=sender,
        sender_name=sender,
        content=body,
        chat_type=chat_type,
        timestamp_ms=1,
        raw={
            "kind": "m.room.message.text",
            "_nio_event": SimpleNamespace(source=source),
        },
    )


VOICE_ENVELOPE = (
    '<narra-system-prompt version="1" mode="voice">\n'
    "Speak casually.\n"
    "</narra-system-prompt>\n\n"
)
VOICE_TRANSCRIPT = "What is the weather today?"


def _parse(t: MatrixTrigger, body: str, *, content_extra: Optional[dict] = None) -> ParsedMessage:
    """Run the real parse_event on a synced text event for ROOM."""
    content: dict[str, Any] = {"msgtype": "m.text", "body": body}
    content.update(content_extra or {})
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": body,
            "server_ts": 1,
            "_nio_event": SimpleNamespace(
                source={"type": "m.room.message", "content": content}
            ),
        }
    )
    assert parsed is not None
    return parsed


class _Harness:
    """Real trigger with only the post-classification side effects stubbed.

    ``_authorize_event`` denies so a dm / group_mention run stops right
    after the gate; ``_enqueue_silent`` records the silent disposition.
    Which of the two fired is the observable CLASSIFY decision.
    """

    def __init__(self, client: _FakeClient, *, allow: bool = False) -> None:
        self.client = client
        self.trigger = MatrixTrigger()
        self.authorize = AsyncMock(return_value=_AuthorizeVerdict(allow=allow))
        self.enqueue_silent = AsyncMock()
        t = self.trigger
        t._clients[t._subscriber_key(_cred())] = client  # type: ignore[assignment]
        setattr(t, "_authorize_event", self.authorize)
        setattr(t, "_send_matrix_notice", AsyncMock())
        setattr(t, "_enqueue_silent", self.enqueue_silent)
        setattr(t, "_ingress_admitted", AsyncMock(return_value=True))

    async def route(self, message: ParsedMessage) -> str:
        """Drive the real ``_process_message`` and report the routing outcome."""
        self.authorize.reset_mock()
        self.enqueue_silent.reset_mock()
        await self.trigger._process_message(_cred(), message)
        if self.enqueue_silent.await_count:
            assert self.authorize.await_count == 0
            return "group_silent"
        assert self.authorize.await_count == 1
        assert self.authorize.await_args is not None
        mentioned = self.authorize.await_args.kwargs["mentioned"]
        return "group_mention" if mentioned else "dm"

    async def count(self) -> int:
        return await self.trigger._get_member_count(self.client, ROOM)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────────
# Fresh invited 1:1 room — the prod scenario
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fresh_room_only_own_join_seen_classifies_dm():
    """Auto-join → sync delivers only the agent's own join → user speaks.
    The REST roster (2) must decide, not the partial sync view (1)."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(
        ROOM, _member_event(AGENT_MXID, "join", prev_membership="invite")
    )

    assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM]


@pytest.mark.asyncio
async def test_fresh_room_only_inviter_join_seen_classifies_dm():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join", displayname="Alice"))

    assert await h.route(_msg()) == "dm"


@pytest.mark.asyncio
async def test_rest_lookup_happens_once_per_room():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)

    for _ in range(3):
        assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM]


# ────────────────────────────────────────────────────────────────────
# Idempotence
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replayed_join_after_authoritative_load_does_not_inflate():
    """The same join event delivered twice (state block + timeline, or a
    since-token rewind) must not count the member twice."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    t = h.trigger
    assert await h.count() == 2

    ev = _member_event(USER_MXID, "join", displayname="Alice")
    t._apply_member_event(ROOM, ev)
    t._apply_member_event(ROOM, ev)

    assert await h.count() == 2
    assert await h.route(_msg()) == "dm"


def test_replayed_join_events_do_not_inflate_parse_event_shape():
    """parse_event gates the pre-classification shape on the roster size;
    duplicated join events must still read as a 2-person room."""
    t = MatrixTrigger()
    for _ in range(2):
        t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
        t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))

    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": "hi",
            "server_ts": 1,
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE


def test_parse_event_unknown_room_is_group():
    t = MatrixTrigger()
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": "hi",
            "server_ts": 1,
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.GROUP


# ────────────────────────────────────────────────────────────────────
# Fail-closed + retry
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_raises_then_retries_on_next_message():
    client = _FakeClient(RuntimeError("homeserver blip"), _roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)

    assert await h.route(_msg()) == "group_silent"
    assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM, ROOM]


@pytest.mark.asyncio
async def test_rest_empty_roster_is_unknown_and_retried():
    """An empty member list is not a shape — it must not be cached as 0."""
    client = _FakeClient([], _roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)

    assert await h.route(_msg()) == "group_silent"
    assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM, ROOM]


@pytest.mark.asyncio
async def test_rest_error_response_is_unknown_and_retried():
    client = _FakeClient(None, _roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)

    assert await h.route(_msg()) == "group_silent"
    assert await h.route(_msg()) == "dm"


@pytest.mark.asyncio
async def test_unverified_sync_roster_is_not_trusted_when_rest_fails():
    """Two join events from sync look like a 1:1 room, but a partial sync
    view is exactly what misled the old counter. Without a successful
    REST lookup the shape is unknown → group_silent (never auto-reply to
    a room we could not verify)."""
    client = _FakeClient(RuntimeError("down"))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))

    assert await h.count() == 0
    assert await h.route(_msg()) == "group_silent"


# ────────────────────────────────────────────────────────────────────
# Real groups + live membership changes
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_three_person_room_silent_unless_mentioned():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    # Partial sync view that would look like a DM on its own.
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))

    assert await h.route(_msg("hey all")) == "group_silent"
    assert await h.route(_msg("hey", mentions=[AGENT_MXID])) == "group_mention"
    assert client.calls == [ROOM]


@pytest.mark.asyncio
async def test_leave_and_join_after_authoritative_load_move_the_shape():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    assert await h.route(_msg()) == "group_silent"

    t._apply_member_event(
        ROOM, _member_event(THIRD_MXID, "leave", prev_membership="join")
    )
    assert await h.route(_msg()) == "dm"

    t._apply_member_event(
        ROOM, _member_event(THIRD_MXID, "join", prev_membership="leave")
    )
    assert await h.route(_msg()) == "group_silent"
    # Live events keep the authoritative roster current — no re-fetch.
    assert client.calls == [ROOM]


@pytest.mark.asyncio
async def test_ban_removes_member_and_invite_does_not_add():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    assert await h.count() == 3

    t._apply_member_event(
        ROOM, _member_event(THIRD_MXID, "ban", prev_membership="join")
    )
    assert await h.count() == 2

    t._apply_member_event(ROOM, _member_event("@carol:h", "invite"))
    assert await h.count() == 2


# ────────────────────────────────────────────────────────────────────
# Authorize payload hint
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_room_member_mxids_reflects_roster():
    client = _FakeClient(_roster(USER_MXID, AGENT_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    await h.count()
    assert t._room_member_mxids(ROOM) == sorted([AGENT_MXID, USER_MXID, THIRD_MXID])

    t._apply_member_event(
        ROOM, _member_event(THIRD_MXID, "ban", prev_membership="join")
    )
    assert t._room_member_mxids(ROOM) == sorted([AGENT_MXID, USER_MXID])


def test_room_member_mxids_falls_back_to_display_name_cache():
    t = MatrixTrigger()
    t._display_name_cache[(ROOM, USER_MXID)] = "Alice"
    assert t._room_member_mxids(ROOM) == [USER_MXID]


# ────────────────────────────────────────────────────────────────────
# Roster changes racing the REST lookup
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_event_during_lookup_triggers_one_refetch():
    """A leave applied by the sync loop while joined_members is in flight
    means the response (3, pre-leave) may predate it. It must not be
    trusted; one immediate re-fetch that stays quiet verifies the room
    within the same message."""
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
        _roster(AGENT_MXID, USER_MXID),
    )
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "join"))
    client.during_lookup = [
        lambda: t._apply_member_event(
            ROOM, _member_event(THIRD_MXID, "leave", prev_membership="join")
        ),
    ]

    assert await h.route(_msg()) == "dm"
    assert ROOM in t._room_roster_authoritative
    assert client.calls == [ROOM, ROOM]


@pytest.mark.asyncio
async def test_lookup_invalidated_twice_fails_closed():
    """Both bounded attempts race a membership change: a response that
    predates a join could pin a real group at 2 (= dm, false auto-reply),
    so the message is classified with an unknown shape and the room stays
    non-authoritative until a later message's lookup is quiet."""
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID),
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, "@carol:h"),
    )
    h = _Harness(client)
    t = h.trigger
    client.during_lookup = [
        lambda: t._apply_member_event(ROOM, _member_event(THIRD_MXID, "join")),
        lambda: t._apply_member_event(ROOM, _member_event("@carol:h", "join")),
    ]

    assert await h.route(_msg()) == "group_silent"
    assert ROOM not in t._room_roster_authoritative
    assert client.calls == [ROOM, ROOM]  # bounded: no third attempt

    assert await h.route(_msg()) == "group_silent"
    assert ROOM in t._room_roster_authoritative
    assert await h.count() == 4


@pytest.mark.asyncio
async def test_replayed_event_during_lookup_does_not_invalidate_it():
    """Only a real roster change counts as a race; replaying a member we
    already hold must not force a retry."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    client.during_lookup = [
        lambda: t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    ]

    assert await h.route(_msg()) == "dm"
    assert ROOM in t._room_roster_authoritative


@pytest.mark.asyncio
async def test_late_landing_lookup_does_not_overwrite_verified_roster():
    """Two lookups for one room overlap; the one that started FIRST (older
    snapshot) lands LAST. It must not replace the roster the other one
    already verified."""
    release_first = asyncio.Event()
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),  # first, stale
        _roster(AGENT_MXID, USER_MXID),  # second, fresh
    )
    h = _Harness(client)
    client.during_lookup = [release_first.wait]

    first = asyncio.create_task(h.count())
    await asyncio.sleep(0)  # first lookup is now parked mid-flight
    assert await h.count() == 2
    release_first.set()
    assert await first == 2
    assert await h.count() == 2


@pytest.mark.asyncio
async def test_own_leave_drops_authority_so_a_rejoin_is_reverified():
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID),
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
    )
    h = _Harness(client)
    t = h.trigger
    assert await h.count() == 2

    t._apply_member_event(
        ROOM, _member_event(AGENT_MXID, "leave", prev_membership="join")
    )
    assert ROOM not in t._room_roster_authoritative
    # Rejoined later: events missed while we were out are not trusted.
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    assert await h.count() == 3
    assert client.calls == [ROOM, ROOM]


# ────────────────────────────────────────────────────────────────────
# Downstream sees the authoritative shape
# ────────────────────────────────────────────────────────────────────


async def _full_path_message(h: _Harness, message: ParsedMessage, monkeypatch) -> ParsedMessage:
    """Route through the real _process_message with authorize allowing,
    and return the message handed to the base pipeline."""
    seen: list[ParsedMessage] = []

    async def _fake_super(self, credential, msg):
        seen.append(msg)

    monkeypatch.setattr(base_mod.ChannelTriggerBase, "_process_message", _fake_super)
    await h.trigger._process_message(_cred(), message)
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
async def test_fresh_dm_reaches_pipeline_as_private(monkeypatch):
    """Prod scenario: only our own join was synced, so parse_event stamped
    GROUP. After the authoritative dm verdict the pipeline (context
    builder room_type, inbox thread type, circuit-refusal throttle) must
    see PRIVATE."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client, allow=True)
    t = h.trigger
    t._apply_member_event(
        ROOM, _member_event(AGENT_MXID, "join", prev_membership="invite")
    )
    parsed = _parse(t, "hello")
    assert parsed.chat_type == ChatType.GROUP  # pre-classification hint

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.PRIVATE


@pytest.mark.asyncio
async def test_group_with_dm_looking_partial_roster_reaches_pipeline_as_group(monkeypatch):
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client, allow=True)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))
    parsed = _parse(t, "hey", content_extra={"m.mentions": {"user_ids": [AGENT_MXID]}})
    assert parsed.chat_type == ChatType.PRIVATE  # partial roster read as 2

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.GROUP


@pytest.mark.asyncio
async def test_group_silent_buffer_receives_group_shape():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))
    parsed = _parse(t, "just chatting")

    await t._process_message(_cred(), parsed)
    h.enqueue_silent.assert_awaited_once()
    assert h.enqueue_silent.await_args is not None
    assert h.enqueue_silent.await_args.args[1].chat_type == ChatType.GROUP


@pytest.mark.asyncio
async def test_voice_stamp_from_partial_roster_is_undone_in_a_group(monkeypatch):
    """parse_event's 1:1 voice gate read a partial roster of 2 and stamped
    rtc_voice; the room is really a group. Voice metadata has no source
    binding, so the group turn must run as plain text with the original
    body (the 1:1-only injection boundary)."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client, allow=True)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))
    body = VOICE_ENVELOPE + VOICE_TRANSCRIPT
    parsed = _parse(
        t,
        body,
        content_extra={
            "m.mentions": {"user_ids": [AGENT_MXID]},
            "ai.netmind.rtc.voice_input": {
                "version": 1,
                "rtc_session_id": "rtc-s1",
                "turn_id": "t1",
                "invocation_id": "inv1",
                "agent_profile_id": "prof1",
                "seq": 1,
                "transcript_final": True,
                "transport": "matrix",
                "voice_instructions": "Reply for a real-time voice call.",
            },
        },
    )
    assert (parsed.raw or {}).get("rtc_voice")  # stamped off the partial roster

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.GROUP
    assert "rtc_voice" not in (handed.raw or {})
    assert handed.content == body


# ────────────────────────────────────────────────────────────────────
# Re-invited rooms are re-verified (real connect() auto-join path)
# ────────────────────────────────────────────────────────────────────


class _SyncResp(SimpleNamespace):
    """Stands in for nio.SyncResponse (connect() isinstance-checks it)."""


async def _drive_auto_join(
    monkeypatch, t: MatrixTrigger, *, join_error=None, join_response=None
) -> list[str]:
    """Run the real connect() for one /sync batch that invites us to ROOM.

    ``join_error`` is raised by ``join`` (transport failure);
    ``join_response`` replaces the success response (nio reports
    server-side failures as a returned ``JoinError``, not an exception).
    """
    joined: list[str] = []

    class _SyncClient:
        def __init__(self, **_kw):
            self.device_id = None
            self.access_token = None
            self.user_id = None
            self._synced = False

        async def sync(self, timeout=None, since=None, full_state=False):
            if self._synced:
                t.running = False
                return _SyncResp(
                    rooms=SimpleNamespace(invite={}, join={}), next_batch="end"
                )
            self._synced = True
            return _SyncResp(
                rooms=SimpleNamespace(invite={ROOM: SimpleNamespace()}, join={}),
                next_batch="b1",
            )

        async def join(self, room_id):
            if join_error is not None:
                raise join_error
            if join_response is not None:
                return join_response
            joined.append(room_id)
            return JoinResponse(room_id)

        async def close(self):
            return None

    monkeypatch.setattr(mt, "AsyncClient", _SyncClient)
    monkeypatch.setattr(mt, "SyncResponse", _SyncResp)
    cred = NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-bearer",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake",
    )
    t.running = True
    async for _ in t.connect(cred):
        pass
    return joined


async def _verified_trigger() -> MatrixTrigger:
    """A trigger that verified ROOM earlier (we were a member before)."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    assert await h.count() == 2
    assert ROOM in h.trigger._room_roster_authoritative
    h.trigger._clients.clear()
    return h.trigger


@pytest.mark.asyncio
async def test_auto_join_of_reinvited_room_drops_its_authority(monkeypatch):
    """We left (our own leave arrives under rooms.leave, which the sync
    loop never walks) and were invited back: the old roster missed every
    membership change in between, so the room must be re-verified."""
    t = await _verified_trigger()

    assert await _drive_auto_join(monkeypatch, t) == [ROOM]
    assert ROOM not in t._room_roster_authoritative


@pytest.mark.asyncio
async def test_failed_auto_join_keeps_authority(monkeypatch):
    """A failed join leaves membership as it was (the invite is retried on
    the next sync), so the verified roster stands."""
    t = await _verified_trigger()

    assert await _drive_auto_join(monkeypatch, t, join_error=RuntimeError("blip")) == []
    assert ROOM in t._room_roster_authoritative


@pytest.mark.asyncio
async def test_join_error_response_keeps_authority(monkeypatch):
    """nio returns JoinError (no exception) for M_FORBIDDEN / rate limits:
    that is a failed join too, not an auto-join."""
    t = await _verified_trigger()

    await _drive_auto_join(
        monkeypatch, t, join_response=JoinError("forbidden", "M_FORBIDDEN")
    )
    assert ROOM in t._room_roster_authoritative


@pytest.mark.asyncio
async def test_auto_join_clears_stale_unverified_leaves(monkeypatch):
    """A leave recorded before we were (re)invited must not shrink the
    roster verified after the auto-join: the member may have come back
    while we were not watching."""
    t = MatrixTrigger()
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave"))

    assert await _drive_auto_join(monkeypatch, t) == [ROOM]
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    assert await t._get_member_count(client, ROOM) == 3  # type: ignore[arg-type]
    assert t._room_joined[ROOM] == {AGENT_MXID, USER_MXID, THIRD_MXID}


# ────────────────────────────────────────────────────────────────────
# Leaves of members the partial roster never saw join
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unseen_member_leave_during_lookup_triggers_refetch():
    """Fresh room, only our own join synced. While joined_members is in
    flight, B leaves; B's join was never seen, so the roster set does not
    change (no generation bump). The snapshot may predate the leave
    ({agent, A, B}) and must not pin the room as a 3-person group: the
    lookup is repeated, and the quiet second snapshot ({agent, A}) wins."""
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
        _roster(AGENT_MXID, USER_MXID),
    )
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(
        ROOM, _member_event(AGENT_MXID, "join", prev_membership="invite")
    )
    client.during_lookup = [
        lambda: t._apply_member_event(
            ROOM, _member_event(THIRD_MXID, "leave", prev_membership="join")
        ),
    ]

    assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM, ROOM]
    assert ROOM in t._room_roster_authoritative
    assert t._room_joined[ROOM] == {AGENT_MXID, USER_MXID}
    assert (ROOM, THIRD_MXID) not in t._display_name_cache
    assert ROOM not in t._room_left_unverified


@pytest.mark.asyncio
async def test_leave_then_rejoin_within_lookup_keeps_the_member():
    """B's (delayed) leave is synced while the request is in flight, but B
    rejoined before the server took the snapshot, which correctly lists B.
    Subtracting B would read a 3-person group as a DM for this message;
    the re-fetch sees B again instead."""
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
    )
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    client.during_lookup = [
        lambda: t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave")),
    ]

    assert await h.route(_msg()) == "group_silent"
    assert t._room_joined[ROOM] == {AGENT_MXID, USER_MXID, THIRD_MXID}
    assert client.calls == [ROOM, ROOM]


@pytest.mark.asyncio
async def test_leave_in_flight_on_both_attempts_fails_closed():
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID),
        _roster(AGENT_MXID, USER_MXID, "@carol:h"),
        _roster(AGENT_MXID, USER_MXID),
    )
    h = _Harness(client)
    t = h.trigger
    client.during_lookup = [
        lambda: t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave")),
        lambda: t._apply_member_event(ROOM, _member_event("@carol:h", "leave")),
    ]

    assert await h.route(_msg()) == "group_silent"
    assert ROOM not in t._room_roster_authoritative
    assert client.calls == [ROOM, ROOM]  # bounded: no third attempt

    # Next message: both leaves are now pre-lookup, the quiet snapshot wins.
    assert await h.route(_msg()) == "dm"
    assert ROOM in t._room_roster_authoritative


@pytest.mark.asyncio
async def test_leave_synced_before_lookup_is_not_subtracted():
    """B's leave was synced BEFORE the lookup started; B then rejoined on
    the server and that join has not synced yet. A snapshot taken after the
    leave is at least as new as it, so it is trusted as-is: subtracting B
    would read a 3-person group as a DM and auto-reply in it."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave"))

    assert await h.route(_msg()) == "group_silent"
    assert ROOM in t._room_roster_authoritative
    assert t._room_joined[ROOM] == {AGENT_MXID, USER_MXID, THIRD_MXID}
    assert client.calls == [ROOM]


@pytest.mark.asyncio
async def test_rejoin_after_unverified_leave_is_not_subtracted():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave"))
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "join"))

    assert await h.count() == 3


@pytest.mark.asyncio
async def test_initial_sync_leave_tombstones_do_not_block_verification():
    """A first sync's state block carries leave/ban events for past
    members. The sync loop applies the state block before yielding the
    batch's messages, so these are recorded BEFORE any lookup starts and
    must not invalidate it: one REST call verifies the room."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event("@old1:h", "leave"))
    t._apply_member_event(ROOM, _member_event("@old2:h", "leave"))
    t._apply_member_event(ROOM, _member_event("@old3:h", "ban"))

    assert await h.route(_msg()) == "dm"
    assert client.calls == [ROOM]
    assert ROOM not in t._room_left_unverified


@pytest.mark.asyncio
async def test_own_leave_clears_unverified_leaves():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID))
    h = _Harness(client)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(THIRD_MXID, "leave"))
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "leave"))

    assert await h.count() == 3


# ────────────────────────────────────────────────────────────────────
# First-message @displayname mention in a fresh room
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_message_displayname_mention_uses_rest_backfill():
    """Our display name is only known from the joined_members roster (our
    own join was not synced). The room shape is primed before mention
    detection, so the FIRST ``@displayname`` message is a mention."""
    roster = [
        SimpleNamespace(user_id=AGENT_MXID, display_name="Helper Bot"),
        SimpleNamespace(user_id=USER_MXID, display_name="Alice"),
        SimpleNamespace(user_id=THIRD_MXID, display_name="Bob"),
    ]
    client = _FakeClient(roster)
    h = _Harness(client)

    assert await h.route(_msg("@Helper Bot can you summarise?")) == "group_mention"
    assert client.calls == [ROOM]


def test_member_event_without_mxid_caches_nothing():
    """No state_key and no sender: there is no member to attach the name
    to, so neither the roster nor the display-name cache may change."""
    t = MatrixTrigger()
    t._apply_member_event(
        ROOM,
        SimpleNamespace(
            state_key="",
            sender="",
            membership="join",
            prev_membership="",
            content={"displayname": "Ghost", "membership": "join"},
        ),
    )
    assert t._display_name_cache == {}
    assert ROOM not in t._room_joined


# ────────────────────────────────────────────────────────────────────
# Managed rooms: the @hybrid-im-bot service sender + room_creation state
# ────────────────────────────────────────────────────────────────────
# Since 2026-09-16 every NarraMessenger managed room carries a third joined
# member, the appservice bot @hybrid-im-bot:<server_name>. A real 1:1 room
# therefore has 3 joined members; counting them raw classified every DM
# (owner rooms included) group_silent after #415.


def _creation_event(room_type: str) -> UnknownEvent:
    """``ai.netmind.room_creation.v1`` as nio parses it off /sync."""
    return UnknownEvent.from_dict(
        {
            "type": ROOM_CREATION,
            "state_key": "",
            "sender": BOT_MXID,
            "event_id": "$create",
            "origin_server_ts": 1,
            "content": {"message_mode": "managed", "room_type": room_type},
        }
    )


@pytest.mark.asyncio
async def test_managed_dm_with_service_bot_reaches_pipeline_as_private(monkeypatch):
    """Prod scenario: fresh room, roster {agent, user, bot}, no creation
    state. The bot is not a participant: the room is a DM."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, BOT_MXID))
    h = _Harness(client, allow=True)
    t = h.trigger
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join", prev_membership="invite"))

    handed = await _full_path_message(h, _parse(t, "hello"), monkeypatch)
    assert handed.chat_type == ChatType.PRIVATE
    assert client.state_calls == [(ROOM, ROOM_CREATION, "")]


@pytest.mark.asyncio
async def test_owner_room_with_service_bot_is_dm():
    owner = "@owner:matrix.netmind.chat"
    client = _FakeClient(_roster(AGENT_MXID, owner, BOT_MXID))
    h = _Harness(client)

    assert await h.route(_msg(sender=owner)) == "dm"
    assert await h.count() == 3  # the roster itself still holds the bot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookalike",
    [
        "@hybrid-im-bot-evil:matrix.netmind.chat",
        "@hybrid-im-bot:other.server",
        "@hybrid-im-bot2:matrix.netmind.chat",
    ],
)
async def test_lookalike_service_bot_is_a_real_member(lookalike):
    """Only the EXACT @hybrid-im-bot on our own server is excluded."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, lookalike))
    h = _Harness(client)

    assert await h.route(_msg()) == "group_silent"
    assert await h.route(_msg("hey", mentions=[AGENT_MXID])) == "group_mention"


@pytest.mark.asyncio
async def test_service_bot_excluded_on_the_agents_own_server_only():
    """server_name comes from the agent's MXID (CN / staging servers)."""
    cn_agent = "@agent-abc:matrix-cn.narramessenger.cn"
    cn_user = "@alice:matrix-cn.narramessenger.cn"
    client = _FakeClient(
        _roster(cn_agent, cn_user, "@hybrid-im-bot:matrix-cn.narramessenger.cn"),
        user_id=cn_agent,
    )
    t = MatrixTrigger()
    cred = NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-bearer",
        backend_base_url="https://api.netmind.chat",
        matrix_user_id=cn_agent,
    )
    assert t._service_sender_mxid(cred) == "@hybrid-im-bot:matrix-cn.narramessenger.cn"
    kind, count = await t._resolve_room_shape(client, ROOM, cred)  # type: ignore[arg-type]
    assert (kind, count) == (None, 2)
    assert t._service_sender_mxid(_cred()) == BOT_MXID


@pytest.mark.asyncio
async def test_creation_state_dm_wins_over_member_count():
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID), room_type="dm"
    )
    h = _Harness(client)

    assert await h.route(_msg("no mention")) == "dm"
    assert await h.route(_msg("again")) == "dm"
    assert len(client.state_calls) == 1  # cached with the verified roster


@pytest.mark.asyncio
async def test_creation_state_group_wins_over_two_members():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID), room_type="group")
    h = _Harness(client)

    assert await h.route(_msg("just chatting")) == "group_silent"
    assert await h.route(_msg("hey", mentions=[AGENT_MXID])) == "group_mention"


@pytest.mark.asyncio
async def test_creation_state_from_sync_needs_no_rest_state_call(monkeypatch):
    """The state block of a /sync batch carries the creation event (nio
    parses the custom type as UnknownEvent); the real connect() loop must
    record it so classification honours it without a REST state call."""
    t = MatrixTrigger()

    class _SyncClient:
        def __init__(self, **_kw):
            self.device_id = None
            self.access_token = None
            self.user_id = None
            self._synced = False

        async def sync(self, timeout=None, since=None, full_state=False):
            if self._synced:
                t.running = False
                return _SyncResp(rooms=SimpleNamespace(invite={}, join={}), next_batch="end")
            self._synced = True
            room = SimpleNamespace(
                state=SimpleNamespace(events=[_creation_event("dm")]),
                timeline=SimpleNamespace(events=[]),
            )
            return _SyncResp(
                rooms=SimpleNamespace(invite={}, join={ROOM: room}), next_batch="b1"
            )

        async def close(self):
            return None

    monkeypatch.setattr(mt, "AsyncClient", _SyncClient)
    monkeypatch.setattr(mt, "SyncResponse", _SyncResp)
    cred = NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-bearer",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake",
    )
    t.running = True
    async for _ in t.connect(cred):
        pass

    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID), room_type="group"
    )
    h = _Harness(client)
    h.trigger = t
    t._clients[t._subscriber_key(_cred())] = client  # type: ignore[assignment]
    for name, mock in (
        ("_authorize_event", h.authorize),
        ("_send_matrix_notice", AsyncMock()),
        ("_enqueue_silent", h.enqueue_silent),
        ("_ingress_admitted", AsyncMock(return_value=True)),
    ):
        setattr(t, name, mock)

    assert await h.route(_msg()) == "dm"
    assert client.state_calls == []


@pytest.mark.asyncio
async def test_creation_state_in_timeline_updates_kind():
    """A room created without the state completes it later: the timeline
    event (applied by the sync loop) overrides the count fallback."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID))
    h = _Harness(client)
    t = h.trigger
    assert await h.route(_msg()) == "group_silent"

    t._apply_room_creation_event(ROOM, _creation_event("dm"))
    assert await h.route(_msg()) == "dm"
    assert len(client.state_calls) == 1


@pytest.mark.asyncio
async def test_transient_state_lookup_failure_is_retried():
    """A non-404 failure is not "no state": the next message asks again."""

    class _FlakyState(_FakeClient):
        async def room_get_state_event(self, room_id, event_type, state_key=""):
            if not self.state_calls:
                self.state_calls.append((room_id, event_type, state_key))
                raise RuntimeError("homeserver blip")
            return await super().room_get_state_event(room_id, event_type, state_key)

    client = _FlakyState(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID), room_type="dm"
    )
    h = _Harness(client)

    assert await h.route(_msg()) == "group_silent"  # fail-closed fallback
    assert await h.route(_msg()) == "dm"
    assert len(client.state_calls) == 2
    assert client.calls == [ROOM]  # the roster stays verified


@pytest.mark.asyncio
async def test_authorize_payload_keeps_the_service_bot():
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, BOT_MXID))
    h = _Harness(client)
    t = h.trigger
    assert await h.route(_msg()) == "dm"

    assert BOT_MXID in t._room_member_mxids(ROOM)
    assert t._room_member_mxids(ROOM) == sorted([AGENT_MXID, USER_MXID, BOT_MXID])


def test_parse_event_shape_ignores_service_bot():
    t = MatrixTrigger()
    for mxid in (AGENT_MXID, USER_MXID, BOT_MXID):
        t._apply_member_event(ROOM, _member_event(mxid, "join"))
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": "hi",
            "server_ts": 1,
            "_our_user_id": AGENT_MXID,
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE


def test_parse_event_shape_honours_known_room_kind():
    t = MatrixTrigger()
    for mxid in (AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID):
        t._apply_member_event(ROOM, _member_event(mxid, "join"))
    t._apply_room_creation_event(ROOM, _creation_event("dm"))
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": "hi",
            "server_ts": 1,
            "_our_user_id": AGENT_MXID,
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE


@pytest.mark.asyncio
async def test_own_rejoin_forgets_room_kind():
    """A dropped authority re-verifies the kind too (state may have changed
    while we were out)."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID), room_type="dm")
    h = _Harness(client)
    t = h.trigger
    assert await h.route(_msg()) == "dm"

    t._drop_roster_authority(ROOM)
    client.room_type = "group"
    assert await h.route(_msg()) == "group_silent"
    assert len(client.state_calls) == 2


@pytest.mark.asyncio
async def test_roster_failure_without_creation_state_stays_fail_closed():
    client = _FakeClient(RuntimeError("down"))
    h = _Harness(client)
    t = h.trigger
    for mxid in (AGENT_MXID, USER_MXID, BOT_MXID):
        t._apply_member_event(ROOM, _member_event(mxid, "join"))

    assert await h.route(_msg()) == "group_silent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_response",
    [
        _state_response({"errcode": "M_FORBIDDEN", "error": "not allowed"}, 403),
        _state_response({}, 502),
        _state_response({"errcode": "M_LIMIT_EXCEEDED", "error": "slow"}, 429),
    ],
    ids=["403", "502", "429"],
)
async def test_non_404_error_response_is_retried_not_cached(error_response):
    """nio maps only 404 to RoomGetStateEventError; a 403 / 5xx / 429 comes
    back as a RoomGetStateEventResponse carrying the error body. That is a
    failed lookup, not "the room declares no kind": retry next message."""
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID), room_type="dm"
    )
    client.state_overrides = [error_response]
    h = _Harness(client)
    t = h.trigger

    assert await h.route(_msg()) == "group_silent"
    assert ROOM not in t._room_kind_checked
    assert await h.route(_msg()) == "dm"
    assert len(client.state_calls) == 2


@pytest.mark.asyncio
async def test_state_response_after_authority_drop_is_discarded():
    """The roster authority (and with it the room kind) was dropped while
    the state request was in flight: the response predates the drop and
    must not be recorded."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID), room_type="dm")
    h = _Harness(client)
    t = h.trigger
    client.during_state = [lambda: t._drop_roster_authority(ROOM)]

    await h.route(_msg())
    assert ROOM not in t._room_kind_checked
    assert ROOM not in t._room_kind



# ────────────────────────────────────────────────────────────────────
# Voice register stays 1:1 by effective member count, not by room kind
# ────────────────────────────────────────────────────────────────────
# Creation state room_type "dm" now routes a room as dm whatever its
# count, and the state's sender is not validated. RTC voice metadata has
# no source binding, so the voice register (voice_instructions injection)
# is only granted when the room really has two participants.


def _voice_extra() -> dict:
    return {
        "ai.netmind.rtc.voice_input": {
            "version": 1,
            "rtc_session_id": "rtc-s1",
            "turn_id": "t1",
            "invocation_id": "inv1",
            "agent_profile_id": "prof1",
            "seq": 1,
            "transcript_final": True,
            "transport": "matrix",
            "voice_instructions": "Reply for a real-time voice call.",
        }
    }


@pytest.mark.asyncio
async def test_declared_dm_with_four_members_routes_dm_without_voice(monkeypatch):
    client = _FakeClient(
        _roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID), room_type="dm"
    )
    h = _Harness(client, allow=True)
    body = VOICE_ENVELOPE + VOICE_TRANSCRIPT
    parsed = _parse(h.trigger, body, content_extra=_voice_extra())

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.PRIVATE  # routed dm (conservative: no voice)
    assert "rtc_voice" not in (handed.raw or {})
    assert handed.content == body


@pytest.mark.asyncio
async def test_parse_event_stamp_is_undone_when_declared_dm_has_more_members(monkeypatch):
    """Kind dm from sync + a partial roster {agent, user} let parse_event
    stamp voice; the verified roster has a third participant, so the
    routed-dm turn must run as plain text."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID))
    h = _Harness(client, allow=True)
    t = h.trigger
    t._apply_room_creation_event(ROOM, _creation_event("dm"))
    t._apply_member_event(ROOM, _member_event(AGENT_MXID, "join"))
    t._apply_member_event(ROOM, _member_event(USER_MXID, "join"))
    body = VOICE_ENVELOPE + VOICE_TRANSCRIPT
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": body,
            "server_ts": 1,
            "_our_user_id": AGENT_MXID,
            "_nio_event": SimpleNamespace(
                source={
                    "type": "m.room.message",
                    "content": {"msgtype": "m.text", "body": body, **_voice_extra()},
                }
            ),
        }
    )
    assert parsed is not None
    assert (parsed.raw or {}).get("rtc_voice")  # stamped off the partial roster

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.PRIVATE
    assert "rtc_voice" not in (handed.raw or {})
    assert handed.content == body


def test_parse_event_voice_gate_needs_two_effective_members():
    """Known kind dm but a 3-participant roster: PRIVATE, yet no voice."""
    t = MatrixTrigger()
    for mxid in (AGENT_MXID, USER_MXID, THIRD_MXID, BOT_MXID):
        t._apply_member_event(ROOM, _member_event(mxid, "join"))
    t._apply_room_creation_event(ROOM, _creation_event("dm"))
    body = VOICE_ENVELOPE + VOICE_TRANSCRIPT
    parsed = t.parse_event(
        {
            "kind": "m.room.message.text",
            "event_id": "$e1",
            "room_id": ROOM,
            "sender_id": USER_MXID,
            "body": body,
            "server_ts": 1,
            "_our_user_id": AGENT_MXID,
            "_nio_event": SimpleNamespace(
                source={
                    "type": "m.room.message",
                    "content": {"msgtype": "m.text", "body": body, **_voice_extra()},
                }
            ),
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE
    assert "rtc_voice" not in (parsed.raw or {})
    assert parsed.content == body


@pytest.mark.asyncio
async def test_managed_one_to_one_with_service_bot_gets_voice(monkeypatch):
    """Prod 1:1 shape {agent, user, bot}, cold roster: the voice register
    is recovered after the authoritative lookup."""
    client = _FakeClient(_roster(AGENT_MXID, USER_MXID, BOT_MXID))
    h = _Harness(client, allow=True)
    parsed = _parse(h.trigger, VOICE_ENVELOPE + VOICE_TRANSCRIPT, content_extra=_voice_extra())
    assert "rtc_voice" not in (parsed.raw or {})  # cold roster: refused at parse

    handed = await _full_path_message(h, parsed, monkeypatch)
    assert handed.chat_type == ChatType.PRIVATE
    assert (handed.raw or {}).get("rtc_voice")
    assert handed.content == VOICE_TRANSCRIPT


# ────────────────────────────────────────────────────────────────────
# Creation event parsing + _classify argument contract
# ────────────────────────────────────────────────────────────────────


def test_creation_event_with_non_empty_state_key_is_ignored():
    t = MatrixTrigger()
    event = UnknownEvent.from_dict(
        {
            "type": ROOM_CREATION,
            "state_key": "@someone:matrix.netmind.chat",
            "sender": USER_MXID,
            "event_id": "$x",
            "origin_server_ts": 1,
            "content": {"message_mode": "managed", "room_type": "dm"},
        }
    )
    t._apply_room_creation_event(ROOM, event)
    assert ROOM not in t._room_kind
    assert ROOM not in t._room_kind_checked


def test_invalid_room_type_clears_a_recorded_kind():
    t = MatrixTrigger()
    t._apply_room_creation_event(ROOM, _creation_event("dm"))
    assert t._room_kind[ROOM] == "dm"

    t._apply_room_creation_event(ROOM, _creation_event("channel"))
    assert ROOM not in t._room_kind
    assert ROOM in t._room_kind_checked


@pytest.mark.asyncio
async def test_classify_rejects_room_kind_without_member_count():
    t = MatrixTrigger()
    with pytest.raises(ValueError):
        await t._classify(
            SimpleNamespace(), _msg(), _cred(), room_kind="dm"  # type: ignore[arg-type]
        )
