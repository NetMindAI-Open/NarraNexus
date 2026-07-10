"""
@file_name: test_matrix_streaming_reply.py
@date: 2026-07-08
@description: MatrixTrigger silent-first streaming — no placeholder.

Contract (2026-07-08 UX refactor):
- Nothing is sent to the room up front; no "💭 Thinking…" placeholder.
- Raw ``AGENT_RESPONSE`` deltas and ``AGENT_THINKING`` are ignored.
- ``narra_progress(text)`` → recorded to backend log ONLY (no room activity).
- ``narra_reply(text)`` → captured; on finalise it is fresh-sent as a new
  message via ``_send_matrix_reply``.
- Silent (no ``narra_reply``, no error) → NO-OP. Timeline stays clean.
- Error (``MessageType.ERROR`` OR outer stream exception) → fresh ``room_send``
  of ``STREAM_ERROR_MARKER`` so the sender knows to retry.
- ``STREAMING_ENABLED=False`` bypasses all of this → atomic path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module import matrix_trigger as mt_mod
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
    _StreamReplyState,
)
from xyz_agent_context.schema.runtime_message import MessageType


ROOM = "!room:matrix.netmind.chat"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="narra-tok",
        backend_base_url="https://api.netmind.chat",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id="@agent-x:matrix.netmind.chat",
        matrix_access_token="syt_x",
    )


def _delta(text: str):
    return SimpleNamespace(message_type=MessageType.AGENT_RESPONSE, delta=text)


def _thinking(text: str):
    return SimpleNamespace(
        message_type=MessageType.AGENT_THINKING, thinking_content=text
    )


def _tool_call(tool_name: str, arguments):
    """ProgressMessage-shaped event — how the runtime actually emits tool
    calls (message_type=PROGRESS, details.tool_name/arguments). ``arguments``
    may be a dict or a JSON string (both shapes occur in the live SDK)."""
    return SimpleNamespace(
        message_type=MessageType.PROGRESS,
        details={"tool_name": tool_name, "arguments": arguments},
    )


@pytest.fixture
def trigger(monkeypatch):
    t = MatrixTrigger()

    calls = {"send": []}

    async def _fake_send(*, homeserver, token, room_id, content, txn_id=None):
        calls["send"].append({"room": room_id, "body": content.get("body", "")})
        return f"$sent{len(calls['send'])}"

    monkeypatch.setattr(mt_mod, "matrix_room_send", _fake_send)
    return t, calls


# ── raw output / thinking are ignored ───────────────────────────────────
@pytest.mark.asyncio
async def test_agent_response_delta_ignored(trigger):
    """Token deltas must NOT touch the room."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_delta("Hello "), state, _cred(), ROOM)
    await t._handle_stream_event(_delta("world"), state, _cred(), ROOM)
    assert calls["send"] == []


@pytest.mark.asyncio
async def test_agent_thinking_ignored(trigger):
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(_thinking("let me think..."), state, _cred(), ROOM)
    assert calls["send"] == []


# ── narra_progress: tool is gone, stray call must not touch the room ───
@pytest.mark.asyncio
async def test_stray_narra_progress_is_inert(trigger):
    """The ``narra_progress`` tool was deleted with this refactor. If an
    older prompt still emits the tool name, the trigger must NOT touch
    the room, must NOT capture a reply, and must NOT flag error. It is
    just an unknown tool call — silently ignored."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("mcp__narramessenger_module__narra_progress",
                   {"text": "Reading the chart…"}),
        state, _cred(), ROOM,
    )
    await t._handle_stream_event(
        _tool_call("narra_progress", {"text": "step 2"}), state, _cred(), ROOM
    )
    assert calls["send"] == []
    assert state.narra_reply_text == ""
    assert state.error_seen is False


# ── narra_reply capture ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_narra_reply_captured(trigger):
    t, _ = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("web_search", {"query": "irrelevant"}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == ""
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "Final answer."}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == "Final answer."


@pytest.mark.asyncio
async def test_narra_reply_last_wins(trigger):
    t, _ = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "first"}), state, _cred(), ROOM
    )
    await t._handle_stream_event(
        _tool_call("narra_reply", {"text": "revised final"}), state, _cred(), ROOM
    )
    assert state.narra_reply_text == "revised final"


@pytest.mark.asyncio
async def test_narra_reply_mcp_prefixed_and_json_args(trigger):
    """MCP-prefixed tool name (substring match) + arguments as a JSON
    string — both must work; this exact combo was the first-live-turn bug."""
    t, _ = trigger
    state = _StreamReplyState()
    await t._handle_stream_event(
        _tool_call("mcp__narramessenger_module__narra_reply",
                   '{"text": "from a json blob"}'),
        state, _cred(), ROOM,
    )
    assert state.narra_reply_text == "from a json blob"


# ── finalise: silent ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalise_silent_no_error_stays_quiet(trigger):
    """Silent-not-reply (no error) → NO room activity at all. This is the
    core behaviour of the 2026-07-08 UX refactor: when the agent chooses
    silence, the room stays as it was — no dot marker, no redact."""
    t, calls = trigger
    state = _StreamReplyState()
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["send"] == []


# ── finalise: error ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_finalise_error_sends_error_marker(trigger):
    """Error during the stream (state.error_seen=True) → fresh-send the
    error marker so the sender knows to retry. Failures MUST stay
    visible; silent-error would look identical to a normal no-reply
    turn."""
    t, calls = trigger
    state = _StreamReplyState(
        error_seen=True,
        last_error_message="LineTooLong: 400, Got more than 131072 bytes...",
    )
    await t._finalize_stream_silent(_cred(), ROOM, state)
    assert calls["send"] == [{"room": ROOM, "body": t.STREAM_ERROR_MARKER}]


@pytest.mark.asyncio
async def test_error_event_sets_error_seen_flag(trigger):
    """MessageType.ERROR during the stream MUST flip the flag so
    finalize picks the error branch."""
    t, _ = trigger
    state = _StreamReplyState()
    err_event = SimpleNamespace(
        message_type=MessageType.ERROR,
        error_type="LineTooLong",
        error_message="Got more than 131072 bytes ...",
    )
    await t._handle_stream_event(err_event, state, _cred(), ROOM)
    assert state.error_seen is True
    assert "LineTooLong" in state.last_error_message or \
        "Got more than" in state.last_error_message


# ── outer try/except: run_stream raising Python exception ──────────────
@pytest.mark.asyncio
async def test_run_stream_exception_surfaces_error_marker(trigger, monkeypatch):
    """PR-review follow-up: the belt-and-suspenders branch in
    _build_and_run_agent_streaming where ``async for event in
    client_stream`` raises a Python exception must ALSO trigger
    STREAM_ERROR_MARKER. This is the path LineTooLong actually takes —
    the SDK transport crashes rather than emitting MessageType.ERROR."""
    t, calls = trigger

    # Minimal builder + runtime plumbing to reach the stream loop.
    class _FakeBuilder:
        async def build_prompt(self, _):
            return "prompt"

        async def build_retrieval_anchor(self):
            return None

    monkeypatch.setattr(
        t, "create_context_builder", lambda *a, **kw: _FakeBuilder()
    )
    monkeypatch.setattr(t, "_resolve_agent_owner", AsyncMock(return_value="owner"))

    async def _boom_stream(**kw):
        # Simulate what LineTooLong looks like: iteration raises a
        # transport-level Python exception.
        if False:  # pragma: no cover — turn function into async gen
            yield
        raise RuntimeError("simulated transport crash (LineTooLong-like)")

    class _FakeRuntimeClient:
        def run_stream(self, **kw):
            return _boom_stream(**kw)

    monkeypatch.setattr(
        "xyz_agent_context.agent_runtime.client.get_agent_runtime_client",
        lambda: _FakeRuntimeClient(),
    )

    msg = SimpleNamespace(
        chat_id=ROOM,
        message_id="$evt",
        sender_id="@u:h",
        content="hi",
        raw={},
        timestamp_ms=0,
        sender_name="U",
    )
    result = await t._build_and_run_agent_streaming(
        _cred(), msg, "U", attachments=None
    )
    assert result == ""
    assert calls["send"] == [{"room": ROOM, "body": t.STREAM_ERROR_MARKER}]


# ── feature flag ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_streaming_disabled_falls_back_to_atomic(trigger, monkeypatch):
    t, _ = trigger
    t.STREAMING_ENABLED = False
    atomic_called = AsyncMock(return_value="atomic-return")
    streaming_called = AsyncMock(return_value="streaming-return")
    monkeypatch.setattr(t, "_build_and_run_agent_atomic", atomic_called)
    monkeypatch.setattr(t, "_build_and_run_agent_streaming", streaming_called)
    msg = SimpleNamespace(
        chat_id=ROOM, message_id="$evt", sender_id="@u:h", content="hi"
    )
    result = await t._build_and_run_agent(_cred(), msg, "U", attachments=None)
    assert result == "atomic-return"
    atomic_called.assert_awaited_once()
    streaming_called.assert_not_awaited()
