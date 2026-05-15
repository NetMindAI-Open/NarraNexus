"""
End-to-end integration test for ChannelTriggerBase.

This is the proof-of-correctness for Phase 1: a fake channel that
subclasses ChannelTriggerBase, drives scripted events through the full
pipeline (dedup → worker → inbox + audit), and verifies side effects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_INGRESS_DROPPED_DEDUP,
    EVENT_INGRESS_PROCESSED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ParsedMessage


# ────────────────────────────────────────────────────────────────────
# Fakes
# ────────────────────────────────────────────────────────────────────


@dataclass
class _FakeCredential:
    agent_id: str = "agent_a"
    app_id: str = "fake_bot_1"


class _FakeContextBuilder(ChannelContextBuilderBase):
    """Bare-bones builder that produces a deterministic prompt."""

    def __init__(self, message: ParsedMessage):
        self._m = message

    async def get_message_info(self):
        return {
            "channel_display_name": "Fake",
            "channel_key": "fake",
            "room_name": "",
            "room_id": self._m.chat_id,
            "room_type": "Direct Message",
            "sender_display_name": self._m.sender_name,
            "sender_id": self._m.sender_id,
            "timestamp": str(self._m.timestamp_ms),
            "my_channel_id": "",
            "message_body": self._m.content,
            "send_tool_name": "fake_send",
        }

    async def get_conversation_history(self, limit):
        return []

    async def get_room_members(self):
        return []


class _FakeTrigger(ChannelTriggerBase):
    """Drives a scripted list of raw events through the base machinery."""

    channel_name = "fake"
    brand_display = "Fake"
    # Reuse an existing WorkingSource value — adding new enum values is
    # Phase 3/4 territory. LARK works for the fake here.
    working_source = WorkingSource.LARK

    # Faster cadence for tests.
    CREDENTIAL_POLL_INTERVAL_SECONDS = 1
    IDLE_POLL_INTERVAL_SECONDS = 1
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 30

    def __init__(self, scripted_events, credential):
        super().__init__(base_workers=2)
        self._scripted = list(scripted_events)
        self._credential = credential
        self._echo_ids: set[str] = set()

    async def load_active_credentials(self):
        return [self._credential]

    async def connect(self, credential):
        # Yield each scripted event then stop, simulating a clean transport
        # disconnect. The base's _subscribe_loop will sleep before retrying;
        # the test ends before that happens.
        for raw in self._scripted:
            yield raw
            # Tiny pause so the consumer (worker) can drain between events
            await asyncio.sleep(0.02)

    def parse_event(self, raw):
        # Fake events of the form {id, from, content, ts_ms, [echo: bool]}
        if raw.get("echo"):
            # Mark this id so is_echo will recognise it
            self._echo_ids.add(raw["id"])
        return ParsedMessage(
            message_id=raw["id"],
            chat_id=raw.get("chat", "C1"),
            sender_id=raw.get("from", "u_alice"),
            sender_name=raw.get("name", "Alice"),
            content=raw.get("content", ""),
            timestamp_ms=raw.get("ts_ms", 1),
        )

    async def is_echo(self, message, credential):
        return message.message_id in self._echo_ids

    async def resolve_sender_name(self, sender_id, credential):
        return f"resolved_{sender_id}"

    def create_context_builder(self, message, credential, agent_id):
        return _FakeContextBuilder(message)


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


async def _wait_for_messages(db_client, channel_id, count, timeout=5.0):
    """Poll bus_messages until ``count`` rows appear or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rows = await db_client.get("bus_messages", {"channel_id": channel_id})
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.05)
    return await db_client.get("bus_messages", {"channel_id": channel_id})


@pytest.mark.asyncio
async def test_full_pipeline_dedup_inbox_and_audit(db_client, monkeypatch):
    """One unique event + one duplicate id → inbox shows only the unique.

    Audit log records the duplicate as ``ingress_dropped_dedup``.
    """
    # Pre-create the agent row so _resolve_agent_owner can find an owner.
    # Tests can omit this — falling back to agent_id is fine — but doing
    # the lookup exercises that code path.
    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "FakeAgent",
        "created_by": "user_owner",
        "is_public": 0,
    })

    # Stub the AgentRuntime.collect_run path so the test doesn't try to
    # spin up a real agent.
    from xyz_agent_context.channel import channel_trigger_base as ctb_mod

    @dataclass
    class _StubResult:
        output_text: str = "agent reply"
        is_error: bool = False
        error: object = None
        raw_items: list = None

        def __post_init__(self):
            if self.raw_items is None:
                self.raw_items = []

    async def _fake_collect_run(*args, **kwargs):
        return _StubResult()

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    # Patch the lazy imports inside _build_and_run_agent
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)

    cred = _FakeCredential(agent_id="agent_a", app_id="fake_bot_1")
    scripted = [
        {"id": "m1", "from": "u_alice", "content": "hello", "ts_ms": 9_999_999_999_999, "chat": "C1"},
        {"id": "m1", "from": "u_alice", "content": "duplicate", "ts_ms": 9_999_999_999_999, "chat": "C1"},
        {"id": "m2", "from": "u_bob", "content": "second", "ts_ms": 9_999_999_999_999, "chat": "C1"},
    ]

    trigger = _FakeTrigger(scripted, cred)
    await trigger.start(db_client)

    try:
        # Two unique events → 4 inbox rows (2 inbound + 2 outbound).
        rows = await _wait_for_messages(db_client, "fake_C1", count=4, timeout=5.0)
    finally:
        await trigger.stop()

    contents = sorted(r["content"] for r in rows if r["from_agent"].startswith("fake_user_"))
    assert contents == ["hello", "second"]
    # Outbound rows are the agent's reply
    out_contents = [r["content"] for r in rows if r["from_agent"] == "agent_a"]
    assert all(c == "agent reply" for c in out_contents)
    assert len(out_contents) == 2

    # Audit table should have at least 2 ingress_processed + 1 ingress_dropped_dedup.
    audits = await db_client.get(
        "channel_trigger_audit", {"channel": "fake"}
    )
    by_type: dict[str, int] = {}
    for a in audits:
        by_type[a["event_type"]] = by_type.get(a["event_type"], 0) + 1

    assert by_type.get(EVENT_INGRESS_PROCESSED, 0) >= 2
    assert by_type.get(EVENT_INGRESS_DROPPED_DEDUP, 0) >= 1


@pytest.mark.asyncio
async def test_echo_messages_are_dropped(db_client, monkeypatch):
    """is_echo=True must result in EVENT_INGRESS_DROPPED_ECHO and no inbox row."""
    from xyz_agent_context.channel.channel_audit_events import EVENT_INGRESS_DROPPED_ECHO

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    @dataclass
    class _Result:
        output_text: str = "ok"
        is_error: bool = False
        error: object = None
        raw_items: list = None

        def __post_init__(self):
            if self.raw_items is None:
                self.raw_items = []

    async def _stub(*a, **kw):
        return _Result()

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _stub)

    cred = _FakeCredential()
    scripted = [
        {"id": "echo_1", "from": cred.app_id, "content": "i am the bot", "echo": True, "ts_ms": 9_999_999_999_999},
        {"id": "real_1", "from": "u_alice", "content": "real one", "ts_ms": 9_999_999_999_999},
    ]
    trigger = _FakeTrigger(scripted, cred)
    await trigger.start(db_client)
    try:
        # Only one unique event → 2 inbox rows.
        rows = await _wait_for_messages(db_client, "fake_C1", count=2, timeout=4.0)
    finally:
        await trigger.stop()

    assert len(rows) == 2

    audits = await db_client.get(
        "channel_trigger_audit", {"channel": "fake", "event_type": EVENT_INGRESS_DROPPED_ECHO}
    )
    assert len(audits) >= 1
