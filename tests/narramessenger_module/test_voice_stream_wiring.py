"""
@file_name: test_voice_stream_wiring.py
@date: 2026-08-06
@description: _handle_stream_event — voice bridge wiring guards.

Locks:
- The claim gates DELTA streams only: the first trigger-owned reply tool
  (speak or narra_reply) to stream deltas claims the bridge (leaf-name
  key); the other tool's deltas stay out, and deltas from non-reply
  tools never enter.
- COMPLETED reply texts (PROGRESS) from either reply tool always enter
  the bridge — preannounce-then-answer may split across tools; the
  equality dedup covers the identical-text dual call.
- The legacy capture (narra_reply_text) is ALWAYS retained alongside the
  bridge: finalize returns early on any non-empty spoken text so it
  cannot double-deliver, and it is the only delivery path left when the
  spoken form sanitizes to nothing.
- With no bridge (text turn), delta events are ignored exactly as before.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from narranexus_plugins.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _StreamReplyState,
)
from narranexus.platform.schema.runtime_message import MessageType


def _cred():
    return SimpleNamespace(agent_id="agent_v")


def _delta(tool="mcp__narramessenger_module__speak", text="Sunny."):
    return SimpleNamespace(
        message_type=MessageType.AGENT_REPLY_DELTA,
        tool_name=tool,
        call_id="c1",
        delta=text,
    )


def _progress(tool, text):
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool, "arguments": {"text": text}, "call_id": "c1"},
    )


@pytest.mark.asyncio
async def test_speak_delta_feeds_bridge_and_nonspeak_does_not():
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    state.voice_bridge.on_reply_delta.assert_awaited_once_with(
        call_id="c1", delta="Sunny."
    )

    await trigger._handle_stream_event(
        _delta(tool="mcp__chat_module__notify_owner"),
        state,
        _cred(),
        "!r",
    )
    assert state.voice_bridge.on_reply_delta.await_count == 1


@pytest.mark.asyncio
async def test_speak_progress_is_authoritative_and_keeps_plain_fallback():
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "Sunny today."),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_called_once_with(
        call_id="c1", text="Sunny today."
    )
    # Raw text survives in the legacy capture for the sanitize-empty
    # case; finalize never reads it when the bridge delivered.
    assert state.narra_reply_text == "Sunny today."


@pytest.mark.asyncio
async def test_narra_reply_claims_bridge_when_unclaimed():
    """A narra_reply on a voice turn with no prior speak activity claims
    the live stream: its text goes to the bridge AND stays in the legacy
    capture. The capture cannot double-deliver (finalize returns early on
    any non-empty spoken text) but it is the only delivery path left when
    the spoken form sanitizes to nothing (emoji-only ack, URL-only reply
    — review 2026-08-13 Critical #2)."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "text version"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_called_once_with(
        call_id="c1", text="text version"
    )
    assert state.narra_reply_text == "text version"


@pytest.mark.asyncio
async def test_narra_reply_delta_feeds_bridge():
    """narra_reply arg deltas stream into the bridge exactly like speak
    deltas — the model's reply-tool choice must not decide whether the
    caller hears the first word live."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _delta(tool="mcp__narramessenger_module__narra_reply", text="Guang"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_reply_delta.assert_awaited_once_with(
        call_id="c1", delta="Guang"
    )


@pytest.mark.asyncio
async def test_nonclaimant_completed_text_still_reaches_the_bridge():
    """The claim gates DELTA streams only. A completed reply text from
    the OTHER reply tool still enters the bridge as a segment: the VOICE
    prompt teaches a preannounce-then-answer two-call pattern, and when
    the model splits it across tools the answer must be spoken, not
    dropped (review 2026-08-13 Important #3). The exact-equality segment
    dedup protects the identical-text dual-call shape."""
    state = _StreamReplyState()
    state.voice_bridge = MagicMock()
    state.voice_bridge.on_reply_delta = AsyncMock()
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "also noted"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_segment_text.assert_called_once_with(
        call_id="c1", text="also noted"
    )
    assert state.narra_reply_text == "also noted"  # sanitize-empty fallback

    # Delta claim still holds: the non-claimant's DELTAS stay out.
    await trigger._handle_stream_event(
        _delta(tool="mcp__narramessenger_module__narra_reply", text="x"),
        state,
        _cred(),
        "!r",
    )
    state.voice_bridge.on_reply_delta.assert_awaited_once()  # speak's only


@pytest.mark.asyncio
async def test_claimant_sanitize_empty_text_keeps_plain_fallback():
    """Review 2026-08-13 Critical #2: an emoji-only reply sanitizes to ""
    — the bridge delivers nothing, so the raw text must survive in the
    legacy capture for finalize's plain fresh-send."""
    from narranexus_plugins.narramessenger_module._voice_delivery import (
        VoiceDeliveryBridge,
    )

    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return "$e1"

    state = _StreamReplyState()
    state.voice_bridge = VoiceDeliveryBridge(send=_send)
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "👍"),
        state,
        _cred(),
        "!r",
    )
    spoken, ok = await state.voice_bridge.close()
    assert spoken is None and ok is True and sent == []
    assert state.narra_reply_text == "👍"  # finalize fresh-sends this


@pytest.mark.asyncio
async def test_narra_reply_stream_end_to_end_no_duplicate_delivery():
    """Real-bridge scenario: narra_reply deltas + authoritative completion
    produce ONE live lifecycle (base + final edit) with the final text —
    never a second plain send of the same content."""
    from narranexus_plugins.narramessenger_module._voice_delivery import (
        VoiceDeliveryBridge,
    )

    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return f"$e{len(sent)}"

    clock_now = [0.0]
    bridge = VoiceDeliveryBridge(send=_send, clock=lambda: clock_now[0])
    state = _StreamReplyState()
    state.voice_bridge = bridge
    trigger = MatrixTrigger()

    for i, piece in enumerate(["广州是", "华南的中心城市。"]):
        clock_now[0] += 1.0
        await trigger._handle_stream_event(
            SimpleNamespace(
                message_type=MessageType.AGENT_REPLY_DELTA,
                tool_name="mcp__narramessenger_module__narra_reply",
                call_id="prov1",
                delta=piece,
            ),
            state,
            _cred(),
            "!r",
        )
    # Real streams carry an EMPTY call_id on PROGRESS (run_collector does
    # not propagate tool_call_id — dev probe 2026-08-07), so the bridge's
    # raw-prefix judge is what keeps this the SAME segment.
    await trigger._handle_stream_event(
        SimpleNamespace(
            message_type=MessageType.PROGRESS,
            details={
                "tool_name": "mcp__narramessenger_module__narra_reply",
                "arguments": {"text": "广州是华南的中心城市。"},
                "call_id": "",
            },
        ),
        state,
        _cred(),
        "!r",
    )
    spoken, ok = await bridge.close()
    assert ok is True
    assert spoken == "广州是华南的中心城市。"
    # The legacy capture retains the raw text (sanitize-empty fallback);
    # finalize never sends it when spoken is non-empty — the no-duplicate
    # property is carried by the single live lifecycle below.
    assert state.narra_reply_text == "广州是华南的中心城市。"
    final = sent[-1]
    final_body = (
        final["m.new_content"]["body"] if "m.new_content" in final else final["body"]
    )
    assert final_body == "广州是华南的中心城市。"


@pytest.mark.asyncio
async def test_narra_reply_progress_only_still_delivers_via_bridge():
    """Provider without arg-delta streaming: the completed narra_reply
    text alone must still ride the bridge (one final send), keeping the
    no-delta path alive."""
    from narranexus_plugins.narramessenger_module._voice_delivery import (
        VoiceDeliveryBridge,
    )

    sent: list[dict] = []

    async def _send(content: dict) -> str:
        sent.append(content)
        return f"$e{len(sent)}"

    bridge = VoiceDeliveryBridge(send=_send)
    state = _StreamReplyState()
    state.voice_bridge = bridge
    trigger = MatrixTrigger()

    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__narra_reply", "只有整段文本。"),
        state,
        _cred(),
        "!r",
    )
    spoken, ok = await bridge.close()
    assert (spoken, ok) == ("只有整段文本。", True)
    assert state.narra_reply_text == "只有整段文本。"  # unread when spoken
    assert len(sent) == 1 and sent[0]["body"] == "只有整段文本。"


@pytest.mark.asyncio
async def test_text_turn_ignores_deltas_as_before():
    state = _StreamReplyState()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(_delta(), state, _cred(), "!r")
    assert state.narra_reply_text == ""


def test_voice_timing_line_shape():
    from narranexus_plugins.narramessenger_module.matrix_trigger import (
        _format_voice_timing,
    )

    line = _format_voice_timing(
        agent_id="agent_v",
        room_id="!call:h",
        rtc_session_id="rtc-s1",
        received_at=10.0,
        applied_at=10.001,
        request_started_at=10.05,
        first_delta_at=11.2,
        first_sent_at=11.3,
        finalized_at=14.0,
    )
    assert line == (
        "[voice-timing] agent=agent_v room=!call:h rtc_session=rtc-s1 "
        "applied_s=0.00 request_s=0.05 first_token_s=1.20 "
        "first_live_s=1.30 finalized_s=4.00"
    )


def test_voice_timing_line_tolerates_missing_stamps():
    from narranexus_plugins.narramessenger_module.matrix_trigger import (
        _format_voice_timing,
    )

    line = _format_voice_timing(
        agent_id="a",
        room_id="!r",
        rtc_session_id="s",
        received_at=10.0,
        applied_at=10.0,
        request_started_at=10.0,
        first_delta_at=None,
        first_sent_at=None,
        finalized_at=None,
    )
    assert "first_token_s=-1.00" in line
    assert "first_live_s=-1.00" in line
    assert "finalized_s=-1.00" in line


@pytest.mark.asyncio
async def test_speak_on_text_turn_is_delivered_not_dead():
    """Review finding #2: without a bridge (text turn), a speak call must
    still reach the room via the legacy capture — never a silent ok:true."""
    state = _StreamReplyState()
    trigger = MatrixTrigger()
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "spoken by mistake"),
        state,
        _cred(),
        "!r",
    )
    assert state.narra_reply_text == "spoken by mistake"

    # Segments concatenate (speak is a multi-call tool).
    await trigger._handle_stream_event(
        _progress("mcp__narramessenger_module__speak", "second part"),
        state,
        _cred(),
        "!r",
    )
    assert state.narra_reply_text == "spoken by mistake second part"


def test_atomic_extract_output_captures_speak_segments():
    """Review finding #16: the atomic path (STREAMING_ENABLED=False) must
    not leave speak as a dead tool either — extract_output joins speak
    segments when no narra_reply was called."""
    trigger = MatrixTrigger()

    def _tool(name, text):
        return {"item": {"type": "tool_call_item", "tool_name": name,
                         "arguments": {"text": text}}}

    result = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "first part."),
        _tool("mcp__narramessenger_module__speak", "second part."),
    ])
    out = trigger.extract_output(result, None, None)
    assert out == "first part. second part."

    # narra_reply stays THE reply when both are present.
    result2 = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "spoken"),
        _tool("mcp__narramessenger_module__narra_reply", "the real answer"),
    ])
    assert trigger.extract_output(result2, None, None) == "the real answer"


def test_atomic_and_streaming_agree_on_mixed_tool_order():
    """Review finding #22: the two paths must give the same answer for the
    same call sequence — ordered fold with speak appending and narra_reply
    replacing (streaming's actual semantics)."""
    trigger = MatrixTrigger()

    def _tool(name, text):
        return {"item": {"type": "tool_call_item", "tool_name": name,
                         "arguments": {"text": text}}}

    # narra_reply first, speak after -> concatenation (streaming behavior).
    result = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__narra_reply", "the answer."),
        _tool("mcp__narramessenger_module__speak", "one more thing."),
    ])
    assert trigger.extract_output(result, None, None) == "the answer. one more thing."

    # speak first, narra_reply after -> narra_reply replaces (last writer).
    result2 = SimpleNamespace(raw_items=[
        _tool("mcp__narramessenger_module__speak", "spoken"),
        _tool("mcp__narramessenger_module__narra_reply", "the real answer"),
    ])
    assert trigger.extract_output(result2, None, None) == "the real answer"
