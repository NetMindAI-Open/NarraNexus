"""
@file_name: test_matrix_reply_and_extract.py
@date: 2026-07-02
@description: MatrixTrigger — reply sender retry policy + extract_output.

Locks:
- Successful send: RoomSendResponse → return True, no audit.
- Rate limit: M_LIMIT_EXCEEDED honors retry_after_ms and retries.
- Permanent auth failure: M_UNKNOWN_TOKEN aborts immediately without
  exhausting the retry budget; leaves the credential for the sync
  loop's next tick to disable.
- Transient exception: raised on room_send → exponential backoff and
  retry; audits after SEND_MAX_ATTEMPTS.
- extract_output: reads narra_reply text (NOT send_message_to_user_directly,
  which is the owner channel); returns
  "" when the tool is absent (silent-not-reply); DOES NOT fall back
  to output_text (agent thinking must not spill into the room).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from nio import RoomSendError, RoomSendResponse

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage


ROOM = "!room:h"


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="tok",
        matrix_user_id="@agent-abc:h",
    )


def _ok_resp() -> RoomSendResponse:
    return RoomSendResponse(event_id="$sent1", room_id=ROOM)


def _err_resp(code: str, retry_after_ms: int = 0) -> RoomSendError:
    err = RoomSendError(message=code, status_code=code, room_id=ROOM)
    err.retry_after_ms = retry_after_ms
    return err


class _AuditRecorder:
    def __init__(self):
        self.calls = []

    async def append(self, event_type, **kwargs):
        self.calls.append((event_type, kwargs))


@pytest.fixture
def trigger():
    t = MatrixTrigger()
    # Shrink backoff so retry tests don't add seconds.
    t.SEND_INITIAL_BACKOFF_MS = 10
    t.SEND_MAX_ATTEMPTS = 3
    audit = _AuditRecorder()
    t._audit_repo = audit  # base's _audit() writes here
    return t, audit


@pytest.mark.asyncio
async def test_send_success_returns_true_no_audit(trigger):
    t, audit = trigger
    fake_client = SimpleNamespace(
        room_send=AsyncMock(return_value=_ok_resp())
    )
    t._clients[t._subscriber_key(_cred())] = fake_client
    ok = await t._send_matrix_reply(_cred(), ROOM, "hi there")
    assert ok is True
    assert fake_client.room_send.await_count == 1
    assert audit.calls == []


@pytest.mark.asyncio
async def test_send_rate_limit_retries_then_succeeds(trigger):
    t, audit = trigger
    responses = [
        _err_resp("M_LIMIT_EXCEEDED", retry_after_ms=1),
        _ok_resp(),
    ]

    async def fake_send(**kwargs):
        return responses.pop(0)

    fake_client = SimpleNamespace(room_send=AsyncMock(side_effect=fake_send))
    t._clients[t._subscriber_key(_cred())] = fake_client
    ok = await t._send_matrix_reply(_cred(), ROOM, "hi")
    assert ok is True
    assert fake_client.room_send.await_count == 2
    assert audit.calls == []


@pytest.mark.asyncio
async def test_send_permanent_auth_failure_gives_up_fast(trigger):
    t, audit = trigger
    fake_client = SimpleNamespace(
        room_send=AsyncMock(return_value=_err_resp("M_UNKNOWN_TOKEN"))
    )
    t._clients[t._subscriber_key(_cred())] = fake_client
    ok = await t._send_matrix_reply(_cred(), ROOM, "hi")
    assert ok is False
    # Permanent failure aborts on the FIRST hit — should NOT burn all
    # three attempts against a token that won't get better.
    assert fake_client.room_send.await_count == 1
    # But it should still audit — visibility matters even for permanent
    # errors (dashboard / owner notification path).
    assert any(evt == "transport_send_failed" for evt, _ in audit.calls)
    _, details = next(
        (evt, kwargs) for evt, kwargs in audit.calls
        if evt == "transport_send_failed"
    )
    assert details["details"]["error_code"] == "M_UNKNOWN_TOKEN"


@pytest.mark.asyncio
async def test_send_transient_exception_retries_then_audits(trigger):
    t, audit = trigger

    async def always_raise(**kwargs):
        raise ConnectionError("network down")

    fake_client = SimpleNamespace(room_send=AsyncMock(side_effect=always_raise))
    t._clients[t._subscriber_key(_cred())] = fake_client
    ok = await t._send_matrix_reply(_cred(), ROOM, "hi")
    assert ok is False
    assert fake_client.room_send.await_count == t.SEND_MAX_ATTEMPTS
    assert any(evt == "transport_send_failed" for evt, _ in audit.calls)


@pytest.mark.asyncio
async def test_send_without_active_client_audits_no_client(trigger):
    t, audit = trigger
    # Do NOT populate _clients — simulate mid-teardown.
    ok = await t._send_matrix_reply(_cred(), ROOM, "hi")
    assert ok is False
    assert audit.calls
    evt, kwargs = audit.calls[0]
    assert evt == "transport_send_failed"
    assert kwargs["details"]["error_code"] == "no_active_client"


def _msg() -> ParsedMessage:
    return ParsedMessage(
        message_id="$e1",
        chat_id=ROOM,
        sender_id="@u:h",
        sender_name="U",
        content="hi",
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1,
        raw={},
    )


def _tool_call_item(tool_name: str, arguments: dict) -> dict:
    """Real raw_items shape from run_collector.collect_run — a dict with a
    nested ``item`` carrying type/tool_name/arguments."""
    return {
        "item": {
            "type": "tool_call_item",
            "tool_name": tool_name,
            "arguments": arguments,
        }
    }


def test_extract_output_reads_narra_reply_tool_call():
    t = MatrixTrigger()
    result = SimpleNamespace(
        raw_items=[
            _tool_call_item(
                "mcp__narramessenger_module__narra_reply",
                {"text": "here is your answer"},
            )
        ],
        output_text="internal thinking that must NOT go to the room",
    )
    text = t.extract_output(result, _msg(), _cred())
    assert text == "here is your answer"


def test_extract_output_reads_narra_reply_stringified_args():
    """arguments may arrive JSON-encoded — must still parse."""
    t = MatrixTrigger()
    result = SimpleNamespace(
        raw_items=[
            _tool_call_item(
                "mcp__narramessenger_module__narra_reply",
                '{"text": "stringified reply"}',
            )
        ],
        output_text="",
    )
    assert t.extract_output(result, _msg(), _cred()) == "stringified reply"


def test_extract_output_returns_empty_when_no_narra_reply():
    """Silent-not-reply: no narra_reply → empty string, NOT a fall-through
    to output_text (the agent's internal thinking, which must not be posted
    to the room)."""
    t = MatrixTrigger()
    result = SimpleNamespace(
        raw_items=[],
        output_text="I've been thinking about this…",
    )
    text = t.extract_output(result, _msg(), _cred())
    assert text == ""


def test_extract_output_ignores_send_message_to_user_directly():
    """send_message_to_user_directly is the OWNER channel, not the room —
    it must NOT be treated as a NarraMessenger reply."""
    t = MatrixTrigger()
    result = SimpleNamespace(
        raw_items=[
            _tool_call_item(
                "mcp__chat_module__send_message_to_user_directly",
                {"content": "note to my owner"},
            )
        ],
        output_text="",
    )
    text = t.extract_output(result, _msg(), _cred())
    assert text == ""


def test_extract_output_ignores_other_tool_calls():
    """A tool call to some other tool (e.g. web_search) does NOT count
    as a reply. Only narra_reply does."""
    t = MatrixTrigger()
    result = SimpleNamespace(
        raw_items=[_tool_call_item("web_search", {"query": "latest news"})],
        output_text="",
    )
    text = t.extract_output(result, _msg(), _cred())
    assert text == ""


# ── memory extractor (MessageSourceRegistry handler) ────────────────────
def test_memory_extractor_recognises_narra_reply_and_send():
    from xyz_agent_context.module.narramessenger_module.narramessenger_module import (
        _extract_narramessenger_reply,
    )
    assert _extract_narramessenger_reply(
        "mcp__narramessenger_module__narra_reply", {"text": "hello"}
    ) == "hello"
    assert _extract_narramessenger_reply(
        "narra_send", {"room_id": "!r", "text": "proactive"}
    ) == "proactive"


def test_memory_extractor_ignores_owner_and_others():
    from xyz_agent_context.module.narramessenger_module.narramessenger_module import (
        _extract_narramessenger_reply,
    )
    # send_message_to_user_directly is the OWNER channel — not a room reply.
    assert _extract_narramessenger_reply(
        "send_message_to_user_directly", {"content": "to owner"}
    ) is None
    assert _extract_narramessenger_reply("web_search", {"query": "x"}) is None
