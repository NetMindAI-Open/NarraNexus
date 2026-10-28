"""
@file_name: test_trigger_envelope_every_channel.py
@author:
@date: 2026-08-07
@description: Every inbound-channel run must carry the turn envelope.

`trigger_extra_data` used to be hand-rolled at four separate sites: twice in
`ChannelTriggerBase` (single message + silent batch), once in `LarkTrigger`
(which overrides `_build_and_run_agent` wholesale) and once in
`MatrixTrigger._build_and_run_agent_streaming` (the DEFAULT NarraMessenger
path). The 2026-08-06 turn envelope — `channel_room_type` /
`channel_reply_kwargs`, which is how `step_3` knows a silent turn was a 1:1
DM — was added to exactly ONE of them.

Consequence: Lark p2p and NarraMessenger DMs reported an empty room type,
so `step_3` classified them as group rooms and the 1:1 no-reply fallback was
dead code on those channels. Same defect class as the ctx-vs-context wiring
bug, twice more, and neither the unit tests (they test functions) nor the
delivery e2e test (it hands `channel_tag` straight to
`_stream_fallback_recovery`) could see it — both bypass trigger wiring.

These tests pin the seam itself, so the NEXT envelope key cannot silently
skip a channel.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.channel_prompts import (
    ROOM_TYPE_DIRECT,
    ROOM_TYPE_GROUP,
)
from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.schema.channel_tag import ChannelTag


class _FakeBuilder:
    """Stands in for a context builder that has run `build_prompt`."""

    def __init__(self, room_type=ROOM_TYPE_DIRECT, reply_kwargs=None):
        self._room_type = room_type
        self._reply_kwargs = reply_kwargs or {}

    def turn_envelope(self):
        return {
            "channel_room_type": self._room_type,
            "channel_reply_kwargs": self._reply_kwargs,
        }


class _Trigger(ChannelTriggerBase):
    """Minimal concrete subclass — we only exercise the pure builder."""

    channel_name = "faux"
    working_source = "faux"

    async def load_active_credentials(self):  # pragma: no cover
        return []

    async def subscribe(self, credential):  # pragma: no cover
        return None

    async def connect(self, credential):  # pragma: no cover
        return None

    def is_echo(self, event, credential):  # pragma: no cover
        return False

    def parse_event(self, event, credential):  # pragma: no cover
        return None

    async def resolve_sender_name(self, message, credential):  # pragma: no cover
        return ""

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover
        return _FakeBuilder()


def _tag():
    return ChannelTag(
        channel="faux", sender_name="Alice", sender_id="u1", room_id="r1"
    )


@pytest.fixture
def trigger():
    return _Trigger.__new__(_Trigger)  # no __init__: the builder is pure


class TestEnvelopeIsAlwaysPresent:
    def test_dm_envelope_reaches_extra_data(self, trigger):
        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor="hi",
            trigger_id="faux_1",
            builder=_FakeBuilder(reply_kwargs={"context_token": "tok"}),
        )
        assert data["channel_room_type"] == ROOM_TYPE_DIRECT
        assert data["channel_reply_kwargs"] == {"context_token": "tok"}

    def test_group_envelope_reaches_extra_data(self, trigger):
        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor=None,
            trigger_id="faux_1",
            builder=_FakeBuilder(room_type=ROOM_TYPE_GROUP),
        )
        assert data["channel_room_type"] == ROOM_TYPE_GROUP

    def test_common_keys_survive(self, trigger):
        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor="anchor",
            trigger_id="faux_1",
            builder=_FakeBuilder(),
        )
        assert data["channel_tag"]["room_id"] == "r1"
        assert data["retrieval_anchor"] == "anchor"
        assert data["trigger_id"] == "faux_1"

    def test_per_path_extras_are_merged(self, trigger):
        """Lark passes source_message_id, the batch path passes
        batch_messages — they must not have to re-implement the common part
        to add their own key."""
        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor=None,
            trigger_id="faux_1",
            builder=_FakeBuilder(),
            source_message_id="m1",
            batch_messages=[{"x": 1}],
        )
        assert data["source_message_id"] == "m1"
        assert data["batch_messages"] == [{"x": 1}]
        assert data["channel_room_type"] == ROOM_TYPE_DIRECT

    def test_no_builder_degrades_to_non_dm(self, trigger):
        """The silent-batch path has no builder. Absent envelope = not a DM
        = no fallback, which is the safe default (that run is explicitly
        not answering anyone)."""
        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor=None,
            trigger_id="faux_batch",
        )
        assert "channel_room_type" not in data

    def test_broken_builder_does_not_break_the_turn(self, trigger):
        class _Boom:
            def turn_envelope(self):
                raise RuntimeError("builder blew up")

        data = trigger.build_trigger_extra_data(
            channel_tag=_tag(),
            retrieval_anchor=None,
            trigger_id="faux_1",
            builder=_Boom(),
        )
        # Degrades to non-DM rather than killing an inbound message.
        assert "channel_room_type" not in data
        assert data["trigger_id"] == "faux_1"


class TestNoTriggerHandRollsTheDict:
    """Grep-level guard. A future channel that hand-rolls
    `trigger_extra_data` (or `extra_data = {"channel_tag": ...}`) would
    reintroduce the bug this seam removes, and no behavioural test would
    notice until someone reports silence on that channel.
    """

    def test_channel_tag_dict_literal_appears_only_in_the_shared_builder(self):
        import pathlib

        from tests._paths import iter_engine_py_files

        REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
        scanned = list(iter_engine_py_files())
        # Batch 6 moved every channel module out of
        # src/narranexus/platform into plugins/builtin.channels.*/src — a
        # scan limited to the platform tree alone would look nowhere near
        # where a future hand-rolled channel would actually be added.
        assert len(scanned) >= 500, (
            f"only {len(scanned)} engine files scanned — the engine source "
            "roots shrank and this guard is scanning far less than the codebase"
        )
        offenders = []
        for path in scanned:
            text = path.read_text(encoding="utf-8")
            if '"channel_tag": channel_tag.to_dict()' not in text:
                continue
            if path.name == "channel_trigger_base.py":
                continue  # the shared builder itself
            offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            "these files build trigger_extra_data by hand instead of calling "
            f"ChannelTriggerBase.build_trigger_extra_data: {offenders}"
        )
