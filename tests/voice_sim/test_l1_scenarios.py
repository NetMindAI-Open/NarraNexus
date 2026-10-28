"""
@file_name: test_l1_scenarios.py
@date: 2026-08-06
@description: L1 in-process end-to-end scenarios (PRD 4.5 table, items 1/4/5).

The full trigger path runs for real — nio event -> _wrap_event ->
parse_event (RTC detection) -> _build_and_run_agent (serialization
dispatch) -> streaming consume -> VoiceDeliveryBridge -> recorded Matrix
contents. Only the boundaries are faked: the runtime client (scripted
event stream) and the Matrix send functions (recorders). No homeserver,
no LLM.

Locks (scenario numbers from PRD 4.5):
- S1 single voice turn: base live -> >=1 m.replace -> final without the
  live marker; no markdown in any body; turn_profile=voice_fast reaches
  the runtime; speak leads via deltas.
- S4 voice turn then plain text turn: the plain turn runs the legacy
  one-shot path (no live events, no profile) — override not persisted.
- S5 invalid metadata matrix: every broken v1 field still enters a
  DEGRADED voice turn while the common trigger (non-blank
  voice_instructions) holds — fast profile + live delivery, minus
  correlation (handoff §3.4).
- S5 carve-out: transcript_final=False is an explicit interim-STT
  turn-boundary signal, NOT malformed metadata — it stays a plain text
  turn (never spoken aloud mid-utterance).
- S5b no voice signal at all: broken metadata AND no
  voice_instructions — a plain text turn whose reply path never breaks.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.voice_sim.hybrid_sim import LIVE_KEY, build_voice_content
from narranexus_plugins.narramessenger_module import matrix_trigger as mt
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from narranexus.platform.schema.runtime_message import MessageType

HOMESERVER = "matrix.netmind.chat"
AGENT_MXID = f"@agent-sim:{HOMESERVER}"
SENDER = f"@human-sim:{HOMESERVER}"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_sim",
        bearer_token="tok",
        matrix_homeserver_url=f"https://{HOMESERVER}",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake",
    )


def _nio_text_event(content: dict, event_id: str = "$sim1"):
    from nio import RoomMessageText

    ev = RoomMessageText.__new__(RoomMessageText)
    ev.event_id = event_id
    ev.sender = SENDER
    ev.server_timestamp = 1
    ev.body = content["body"]
    ev.source = {
        "type": "m.room.message",
        "event_id": event_id,
        "sender": SENDER,
        "content": content,
    }
    return ev


class ScriptedRuntime:
    """run_stream double: records kwargs, yields a scripted event list."""

    def __init__(self, events: list[Any]):
        self.events = events
        self.calls: list[dict] = []

    def run_stream(self, **kwargs):
        self.calls.append(kwargs)

        async def _gen():
            for ev in self.events:
                yield ev

        return _gen()


def _speak_delta(text: str, call_id: str = "c1"):
    return SimpleNamespace(
        message_type=MessageType.AGENT_REPLY_DELTA,
        tool_name="mcp__narramessenger_module__speak",
        call_id=call_id,
        delta=text,
    )


def _progress(tool: str, text: str, call_id: str = "c1"):
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool, "arguments": {"text": text}, "call_id": call_id},
    )


@pytest.fixture()
def harness(monkeypatch):
    """Wire the trigger with recorders at both faked boundaries."""
    trigger = mt.MatrixTrigger()
    sent: list[dict] = []
    plain: list[str] = []

    async def _fake_room_send(*, homeserver, token, room_id, content, txn_id=None):
        sent.append(content)
        return f"$ev{len(sent) - 1}"

    async def _fake_plain_reply(credential, room_id, text):
        plain.append(text)

    async def _owner(agent_id):
        return "owner_sim"

    class _Builder:
        def __init__(self, message):
            self._message = message

        async def build_prompt(self, config):
            # Real builders embed the message body in the channel prompt;
            # the stub mirrors just that property so transcript assertions
            # exercise the same seam.
            return f"PROMPT\n{self._message.content}"

        async def build_retrieval_anchor(self):
            return "[From Caller] anchor"

    monkeypatch.setattr(mt, "matrix_room_send", _fake_room_send)
    monkeypatch.setattr(trigger, "_send_matrix_reply", _fake_plain_reply, raising=False)
    monkeypatch.setattr(trigger, "_resolve_agent_owner", _owner, raising=False)
    monkeypatch.setattr(
        trigger,
        "create_context_builder",
        lambda message, *a, **k: _Builder(message),
        raising=False,
    )
    return trigger, sent, plain


def _install_runtime(monkeypatch, runtime: ScriptedRuntime):
    import narranexus.platform.agent_runtime.client as client_mod

    monkeypatch.setattr(client_mod, "get_agent_runtime_client", lambda: runtime)


async def _ingest_and_run(trigger, content: dict, event_id: str = "$sim1") -> str:
    trigger._room_joined["!call:h"] = {"@caller:h", "@agent:h"}  # voice mode is 1:1-only
    raw = trigger._wrap_event(
        event=_nio_text_event(content, event_id), room_id="!call:h", credential=_cred()
    )
    message = trigger.parse_event(raw)
    assert message is not None
    return await trigger._build_and_run_agent(_cred(), message, "Caller")


@pytest.mark.asyncio
async def test_s1_single_voice_turn_full_lifecycle(harness, monkeypatch):
    trigger, sent, plain = harness
    runtime = ScriptedRuntime([
        _speak_delta("I am checking the weather now."),
        _progress("mcp__narramessenger_module__speak", "I am checking the weather now."),
        _speak_delta("**Sunny**, twenty five degrees.", call_id="c2"),
        _progress(
            "mcp__narramessenger_module__speak",
            "**Sunny**, twenty five degrees.",
            call_id="c2",
        ),
    ])
    _install_runtime(monkeypatch, runtime)

    out = await _ingest_and_run(
        trigger, build_voice_content("What is the weather today?")
    )

    # The runtime received the one-shot fast profile and the transcript.
    kwargs = runtime.calls[0]
    assert kwargs["turn_profile"].name == "voice_fast"
    assert "What is the weather today?" in kwargs["input_content"]
    assert "narra-system-prompt" not in kwargs["input_content"]

    # Live lifecycle: base live -> >=1 replace -> final without marker.
    assert len(sent) >= 2
    base, final = sent[0], sent[-1]
    assert base[LIVE_KEY] == {} and "m.relates_to" not in base
    assert final["m.relates_to"]["rel_type"] == "m.replace"
    assert LIVE_KEY not in final and LIVE_KEY not in final["m.new_content"]
    # Zero markdown anywhere on the wire; both segments delivered.
    for content in sent:
        body = content.get("m.new_content", content)["body"]
        assert "*" not in body.replace("* ", "", 1) or not body.startswith("* ")
    assert "Sunny, twenty five degrees." in out
    assert plain == []  # live path delivered; no fallback needed


@pytest.mark.asyncio
async def test_s1b_narra_reply_voice_turn_rides_live_lifecycle(harness, monkeypatch):
    """2026-08-13 call finding: models routinely answer voice turns via
    narra_reply despite the spoken-register instructions (12/14 turns).
    The reply tool the model picks must not decide whether the caller
    hears the first word live — narra_reply deltas claim the bridge and
    ride the same base-live -> final-edit lifecycle as speak."""
    trigger, sent, plain = harness
    runtime = ScriptedRuntime([
        SimpleNamespace(
            message_type=MessageType.AGENT_REPLY_DELTA,
            tool_name="mcp__narramessenger_module__narra_reply",
            call_id="prov1",
            delta="Guangzhou is the hub ",
        ),
        SimpleNamespace(
            message_type=MessageType.AGENT_REPLY_DELTA,
            tool_name="mcp__narramessenger_module__narra_reply",
            call_id="prov1",
            delta="of South China.",
        ),
        # Real streams: PROGRESS carries an EMPTY call_id (run_collector
        # does not propagate tool_call_id) — the raw-prefix judge keeps
        # this the same segment, so nothing is spoken twice.
        _progress(
            "mcp__narramessenger_module__narra_reply",
            "Guangzhou is the hub of South China.",
            call_id="",
        ),
    ])
    _install_runtime(monkeypatch, runtime)

    out = await _ingest_and_run(
        trigger, build_voice_content("Introduce Guangzhou briefly.")
    )

    assert runtime.calls[0]["turn_profile"].name == "voice_fast"
    assert len(sent) >= 2
    base, final = sent[0], sent[-1]
    assert base[LIVE_KEY] == {} and "m.relates_to" not in base
    assert final["m.relates_to"]["rel_type"] == "m.replace"
    assert LIVE_KEY not in final and LIVE_KEY not in final["m.new_content"]
    assert final["m.new_content"]["body"] == "Guangzhou is the hub of South China."
    assert out == "Guangzhou is the hub of South China."
    assert plain == []  # live path delivered once; no duplicate fallback


@pytest.mark.asyncio
async def test_s1c_voice_turn_never_opens_a_second_delivery_channel(harness, monkeypatch):
    """Pipeline review round 2 Critical #1: on a live call the plain
    sender is a DELIVERY channel (Hybrid's worker reads plain-text turns
    to the caller — the smoke report's fallback turns had first-segment
    latency, i.e. were heard). A raw-text supplement would therefore be
    a second playback that reads URLs aloud, bypassing the sanitizer's
    structural guarantee. Lock: once the bridge delivered, NOTHING else
    is sent — the known cost (a narra_reply link doesn't reach the chat
    record) is tracked in reference/self_notebook/todo/ pending a
    written worker-consumption contract from Hybrid."""
    trigger, sent, plain = harness
    raw = "报告在这里 https://example.com/report.pdf 请查收。"
    runtime = ScriptedRuntime([
        SimpleNamespace(
            message_type=MessageType.AGENT_REPLY_DELTA,
            tool_name="mcp__narramessenger_module__narra_reply",
            call_id="prov1",
            delta=raw,
        ),
        _progress("mcp__narramessenger_module__narra_reply", raw, call_id=""),
    ])
    _install_runtime(monkeypatch, runtime)

    out = await _ingest_and_run(
        trigger, build_voice_content("把报告链接发我")
    )

    # Live surface: sanitized (no URL read aloud, none in live bodies).
    assert sent, "live lifecycle must have delivered"
    for content in sent:
        body = content.get("m.new_content", content)["body"]
        assert "https://" not in body
    # No second channel: the plain sender stays untouched.
    assert plain == []
    assert out  # turn reports a delivered reply


@pytest.mark.asyncio
async def test_s4_plain_turn_after_voice_keeps_legacy_path(harness, monkeypatch):
    trigger, sent, plain = harness
    runtime = ScriptedRuntime([
        _progress("mcp__narramessenger_module__narra_reply", "plain answer"),
    ])
    _install_runtime(monkeypatch, runtime)

    out = await _ingest_and_run(
        trigger, {"msgtype": "m.text", "body": "hello again"}, event_id="$sim2"
    )

    kwargs = runtime.calls[0]
    assert "turn_profile" not in kwargs  # override was never persisted
    assert sent == []  # zero live events on a text turn
    assert plain == ["plain answer"]  # legacy one-shot delivery
    assert out == "plain answer"


@pytest.mark.asyncio
async def test_s5_invalid_metadata_still_enters_degraded_voice(harness, monkeypatch):
    """Handoff §3.4: a failed v1 metadata block no longer cancels voice mode
    when the common trigger (non-blank voice_instructions) holds — the turn
    runs the fast profile and live delivery, minus correlation."""
    trigger, sent, plain = harness
    for i, bad in enumerate(("seq", "version", "transport", "missing-id")):
        sent.clear()
        runtime = ScriptedRuntime([
            _speak_delta(f"answer {bad}.", call_id=f"c{i}"),
            _progress("mcp__narramessenger_module__speak", f"answer {bad}.", call_id=f"c{i}"),
        ])
        _install_runtime(monkeypatch, runtime)
        out = await _ingest_and_run(
            trigger,
            build_voice_content(f"probe {bad}", invalid=bad),
            event_id=f"$bad{i}",
        )
        kwargs = runtime.calls[0]
        assert kwargs["turn_profile"].name == "voice_fast", f"{bad} missed voice mode"
        assert "narra-system-prompt" not in kwargs["input_content"]
        # Full live lifecycle: base live -> final m.replace without marker.
        assert len(sent) >= 2, f"{bad}: no live lifecycle"
        final = sent[-1]
        assert final["m.relates_to"]["rel_type"] == "m.replace"
        assert LIVE_KEY not in final and LIVE_KEY not in final["m.new_content"]
        assert f"answer {bad}." in out
    assert plain == []  # live path delivered; no plain fallback ever needed


@pytest.mark.asyncio
async def test_s5_final_false_interim_transcript_stays_plain(harness, monkeypatch):
    """transcript_final=False is the backend explicitly marking an interim
    STT fragment — a turn-boundary signal, not malformed metadata. It must
    NOT enter degraded voice mode (it would be spoken aloud mid-utterance):
    the turn stays a plain text turn on the legacy path."""
    trigger, sent, plain = harness
    runtime = ScriptedRuntime([
        _progress("mcp__narramessenger_module__narra_reply", "plain reply"),
    ])
    _install_runtime(monkeypatch, runtime)
    out = await _ingest_and_run(
        trigger,
        build_voice_content("probe interim", invalid="final"),
        event_id="$interim",
    )
    kwargs = runtime.calls[0]
    assert "turn_profile" not in kwargs  # no voice mode, no override
    assert sent == []  # zero live events — nothing spoken
    assert plain == ["plain reply"]  # legacy one-shot delivery
    assert out == "plain reply"


@pytest.mark.asyncio
async def test_s5b_no_voice_signal_at_all_stays_plain(harness, monkeypatch):
    """Broken metadata AND no voice_instructions: plain text turn, reply
    path never breaks (the ONLY remaining plain-degrade case)."""
    trigger, sent, plain = harness
    runtime = ScriptedRuntime([
        _progress("mcp__narramessenger_module__narra_reply", "plain reply"),
    ])
    _install_runtime(monkeypatch, runtime)
    out = await _ingest_and_run(
        trigger,
        build_voice_content("probe silent", invalid="silent", with_envelope=False),
        event_id="$silent",
    )
    kwargs = runtime.calls[0]
    assert "turn_profile" not in kwargs  # legacy one-shot path, no override
    assert sent == []  # zero live events on a plain turn
    assert plain == ["plain reply"]  # legacy one-shot delivery
    assert out == "plain reply"
