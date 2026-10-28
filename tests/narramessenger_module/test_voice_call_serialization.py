"""
@file_name: test_voice_call_serialization.py
@date: 2026-08-06
@description: F28 per-room voice serialization — one run at a time, merged queue.

Locks:
- Two overlapping utterances in ONE room run strictly serially;
  the one that queued merges into a single follow-up turn whose content
  concatenates the transcripts in arrival order.
- The key is the ROOM (≡ the call under the 1:1 gate), NOT rtc_session_id:
  strict and degraded turns of one call serialize together — a per-session
  key would split-brain into two concurrent TTS streams the moment one
  utterance's metadata failed validation.
- Different rooms stay fully parallel (no cross-call coupling).
- A non-voice message never enters the voice queue (dispatch unchanged).
- Call state dies when the call idles (no leak in _voice_calls).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from narranexus.platform.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _voice_msg(text: str, session: str = "rtc-s1") -> ParsedMessage:
    return ParsedMessage(
        message_id=f"$m-{text[:6]}",
        chat_id="!call:h",
        sender_id="@human:h",
        sender_name="Caller",
        content=text,
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw={"rtc_voice": {"rtc_session_id": session, "turn_id": "t",
                           "invocation_id": "i", "agent_profile_id": "p",
                           "voice_instructions": None}},
    )


class _SlowRunner:
    """Records calls; each run blocks until released."""

    def __init__(self):
        self.calls: list[tuple[str, asyncio.Event]] = []

    async def __call__(self, credential, message, sender_name, *, attachments=None):
        gate = asyncio.Event()
        self.calls.append((message.content, gate))
        await gate.wait()
        return message.content


@pytest.mark.asyncio
async def test_same_call_serializes_and_merges():
    trigger = MatrixTrigger()
    runner = _SlowRunner()
    trigger._build_and_run_agent_streaming = runner  # type: ignore[assignment]

    t1 = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("first."), "Caller")
    )
    await asyncio.sleep(0)  # first run is now in flight
    assert len(runner.calls) == 1

    t2 = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("second."), "Caller")
    )
    t3 = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("third."), "Caller")
    )
    await asyncio.sleep(0)
    # Buffered, NOT run concurrently.
    assert len(runner.calls) == 1

    runner.calls[0][1].set()  # finish the first run
    await asyncio.sleep(0.01)
    # The two queued utterances merged into ONE follow-up run.
    assert len(runner.calls) == 2
    assert runner.calls[1][0] == "second.\nthird."

    runner.calls[1][1].set()
    await asyncio.gather(t1, t2, t3)
    assert trigger._voice_calls == {}  # idle call state cleaned up


@pytest.mark.asyncio
async def test_different_sessions_same_room_serialize():
    """Room is the key, not rtc_session_id: two turns of one call that
    carry different session ids (e.g. metadata drift across a reconnect)
    must still run one at a time — a per-session key would give each its
    own drain loop and interleave two TTS streams on the one call."""
    trigger = MatrixTrigger()
    runner = _SlowRunner()
    trigger._build_and_run_agent_streaming = runner  # type: ignore[assignment]

    a = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("a.", session="s-A"), "C")
    )
    await asyncio.sleep(0)
    assert len(runner.calls) == 1
    b = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("b.", session="s-B"), "C")
    )
    await asyncio.sleep(0)
    assert len(runner.calls) == 1  # buffered behind the same room key
    runner.calls[0][1].set()
    await asyncio.sleep(0.01)
    assert [c[0] for c in runner.calls] == ["a.", "b."]
    runner.calls[1][1].set()
    await asyncio.gather(a, b)
    assert trigger._voice_calls == {}


@pytest.mark.asyncio
async def test_strict_and_degraded_turns_of_one_call_serialize():
    """The review's mixed-level scenario: a strict turn (full rtc ids)
    and a degraded turn (empty ids) in the SAME room are the same call
    and must share one drain loop."""
    trigger = MatrixTrigger()
    runner = _SlowRunner()
    trigger._build_and_run_agent_streaming = runner  # type: ignore[assignment]

    strict = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("strict."), "Caller")
    )
    await asyncio.sleep(0)
    assert len(runner.calls) == 1
    degraded = asyncio.create_task(
        trigger._build_and_run_agent(None, _degraded_msg("degraded."), "Caller")
    )
    await asyncio.sleep(0)
    assert len(runner.calls) == 1  # degraded turn buffered, not parallel
    runner.calls[0][1].set()
    await asyncio.sleep(0.01)
    assert [c[0] for c in runner.calls] == ["strict.", "degraded."]
    runner.calls[1][1].set()
    await asyncio.gather(strict, degraded)
    assert trigger._voice_calls == {}


@pytest.mark.asyncio
async def test_different_rooms_run_in_parallel():
    trigger = MatrixTrigger()
    runner = _SlowRunner()
    trigger._build_and_run_agent_streaming = runner  # type: ignore[assignment]

    a = asyncio.create_task(
        trigger._build_and_run_agent(None, _voice_msg("a."), "C")
    )
    b = asyncio.create_task(
        trigger._build_and_run_agent(
            None, replace(_voice_msg("b."), chat_id="!other:h"), "C"
        )
    )
    await asyncio.sleep(0)
    assert len(runner.calls) == 2  # both in flight simultaneously
    for _, gate in runner.calls:
        gate.set()
    await asyncio.gather(a, b)
    assert trigger._voice_calls == {}


@pytest.mark.asyncio
async def test_non_voice_message_bypasses_voice_queue():
    trigger = MatrixTrigger()
    streaming = AsyncMock(return_value="ok")
    trigger._build_and_run_agent_streaming = streaming  # type: ignore[assignment]
    msg = replace(_voice_msg("hello"), raw={})
    out = await trigger._build_and_run_agent(None, msg, "Caller")
    assert out == "ok"
    assert trigger._voice_calls == {}
    streaming.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_kill_switch_degrades_voice_to_text_turn(monkeypatch):
    """Review finding #9: STREAMING_ENABLED is the streaming kill switch;
    voice turns must respect it by degrading to a plain text turn (no
    voice queue, no rtc_voice reaching the builder/modules)."""
    trigger = MatrixTrigger()
    monkeypatch.setattr(trigger, "STREAMING_ENABLED", False)
    atomic = AsyncMock(return_value="plain")
    trigger._build_and_run_agent_atomic = atomic  # type: ignore[assignment]

    out = await trigger._build_and_run_agent(None, _voice_msg("hello."), "Caller")

    assert out == "plain"
    assert trigger._voice_calls == {}
    passed_msg = atomic.await_args.args[1]
    assert "rtc_voice" not in (passed_msg.raw or {})


def _degraded_msg(text: str, room: str = "!call:h") -> ParsedMessage:
    """Handoff §3.4 DEGRADED turn: empty binding IDs, per-room key."""
    return replace(
        _voice_msg(text),
        chat_id=room,
        raw={"rtc_voice": {"rtc_session_id": "", "turn_id": "",
                           "invocation_id": "", "agent_profile_id": "",
                           "voice_instructions": "Speak for a call.",
                           "degraded": True}},
    )


@pytest.mark.asyncio
async def test_degraded_turns_serialize_per_room_and_parallel_across_rooms():
    """Degraded turns key on (agent, room) like every voice turn:
    same-room degraded turns run one at a time, while degraded turns
    in different rooms stay fully parallel."""
    trigger = MatrixTrigger()
    runner = _SlowRunner()
    trigger._build_and_run_agent_streaming = runner  # type: ignore[assignment]

    t1 = asyncio.create_task(
        trigger._build_and_run_agent(None, _degraded_msg("first."), "Caller")
    )
    await asyncio.sleep(0)  # first same-room run is now in flight
    assert len(runner.calls) == 1

    t2 = asyncio.create_task(
        trigger._build_and_run_agent(None, _degraded_msg("second."), "Caller")
    )
    other = asyncio.create_task(
        trigger._build_and_run_agent(
            None, _degraded_msg("elsewhere.", room="!other:h"), "Caller"
        )
    )
    await asyncio.sleep(0)
    # Same room buffered; the other room ran immediately in parallel.
    assert [c[0] for c in runner.calls] == ["first.", "elsewhere."]

    runner.calls[0][1].set()  # finish the first same-room run
    await asyncio.sleep(0.01)
    # The buffered same-room utterance now runs as the follow-up turn.
    assert [c[0] for c in runner.calls] == ["first.", "elsewhere.", "second."]

    for _, gate in runner.calls[1:]:
        gate.set()
    await asyncio.gather(t1, t2, other)
    assert trigger._voice_calls == {}  # both room keys cleaned up
