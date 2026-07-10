"""
@file_name: test_error_fallback_send.py
@author: NarraNexus
@date: 2026-07-07
@description: ChannelTriggerBase error-fallback: when an IM run fails, the
trigger must surface a short error INTO the channel (so the user can tell
"agent failed" from "agent stayed silent"), but must NEVER send on a clean
run — intended silence (group non-@ / nothing to add) is respected.

Bug: slack/discord/telegram/wechat only wrote errors to the inbox, never to
the channel, so a failed run vanished as silence. Fix routes the error through
the new ``send_channel_reply`` hook via ``_build_and_run_agent``.

Gating (mirrors chat's fallback logic):
  - result.is_error + agent had NOT replied yet -> send error to channel.
  - result.is_error + agent already replied (partial_reply_then_error) -> skip
    (don't double-message).
  - clean run (no error), silent or replied -> never send (respect silence).
  - run_and_collect raises -> still notify (no silent crash).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from xyz_agent_context.channel.channel_trigger_base import CHANNEL_SILENT_SENTINEL
from xyz_agent_context.schema.parsed_message import ParsedMessage
from tests.channel.test_mock_channel_trigger_integration import (
    _FakeCredential,
    _FakeTrigger,
)


@dataclass
class _StubResult:
    output_text: str = ""
    is_error: bool = False
    error: object = None
    raw_items: list = field(default_factory=list)
    scraped: str | None = None  # what extract_output should return


@dataclass
class _StubError:
    error_type: str = "BoomError"
    error_message: str = "boom"


class _RecordingTrigger(_FakeTrigger):
    """Fake trigger that records channel sends and lets each test dictate the
    scraped reply (agent-sent-a-reply vs stayed-silent)."""

    def __init__(self, cred):
        super().__init__([], cred)
        self.sent: list[str] = []

    def extract_output(self, result, message, credential) -> str:
        return getattr(result, "scraped", None) or CHANNEL_SILENT_SENTINEL

    async def send_channel_reply(self, credential, message, text) -> None:
        self.sent.append(text)


def _msg() -> ParsedMessage:
    return ParsedMessage(
        message_id="m1", chat_id="C1", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )


async def _drive(trigger, db_client, monkeypatch, *, result=None, raises=None):
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    async def _fake_collect_run(*_a, **_k):
        if raises is not None:
            raise raises
        return result

    class _FakeAgentRuntime:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)
    trigger._db = db_client
    return await trigger._build_and_run_agent(
        trigger._credential, _msg(), "Alice", attachments=[]
    )


@pytest.mark.asyncio
async def test_error_with_no_reply_sends_fallback_to_channel(db_client, monkeypatch):
    trigger = _RecordingTrigger(_FakeCredential(agent_id="agent_a"))
    result = _StubResult(is_error=True, error=_StubError(), scraped=None)
    ret = await _drive(trigger, db_client, monkeypatch, result=result)

    assert len(trigger.sent) == 1  # error surfaced into the channel
    assert trigger.sent[0] == ret  # same friendly text goes to inbox + channel
    assert "error" in ret.lower()


@pytest.mark.asyncio
async def test_error_after_reply_does_not_double_send(db_client, monkeypatch):
    trigger = _RecordingTrigger(_FakeCredential(agent_id="agent_a"))
    # Agent DID send a real reply before the failure (partial_reply_then_error).
    result = _StubResult(is_error=True, error=_StubError(), scraped="here is your answer")
    await _drive(trigger, db_client, monkeypatch, result=result)

    assert trigger.sent == []  # user already heard from the agent — no double-message


@pytest.mark.asyncio
async def test_clean_silent_run_never_sends(db_client, monkeypatch):
    trigger = _RecordingTrigger(_FakeCredential(agent_id="agent_a"))
    # No error; agent chose silence (group non-@ / nothing to add).
    result = _StubResult(is_error=False, scraped=None)
    ret = await _drive(trigger, db_client, monkeypatch, result=result)

    assert trigger.sent == []  # intended silence is respected
    assert ret == CHANNEL_SILENT_SENTINEL


@pytest.mark.asyncio
async def test_clean_reply_does_not_send(db_client, monkeypatch):
    trigger = _RecordingTrigger(_FakeCredential(agent_id="agent_a"))
    result = _StubResult(is_error=False, scraped="the agent's own reply")
    await _drive(trigger, db_client, monkeypatch, result=result)

    assert trigger.sent == []  # agent already delivered via its own tool


@pytest.mark.asyncio
async def test_run_raising_still_notifies_channel(db_client, monkeypatch):
    trigger = _RecordingTrigger(_FakeCredential(agent_id="agent_a"))
    ret = await _drive(trigger, db_client, monkeypatch, raises=RuntimeError("kaboom"))

    assert len(trigger.sent) == 1  # a hard crash must not vanish into silence
    assert trigger.sent[0] == ret


@pytest.mark.asyncio
async def test_send_failure_is_swallowed(db_client, monkeypatch):
    class _BoomTrigger(_RecordingTrigger):
        async def send_channel_reply(self, credential, message, text) -> None:
            raise RuntimeError("channel API down")

    trigger = _BoomTrigger(_FakeCredential(agent_id="agent_a"))
    result = _StubResult(is_error=True, error=_StubError(), scraped=None)
    # Must not raise — a send failure cannot break inbox recording.
    ret = await _drive(trigger, db_client, monkeypatch, result=result)
    assert "error" in ret.lower()
