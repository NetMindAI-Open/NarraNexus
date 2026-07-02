"""
@file_name: test_failure_notification.py
@author: Bin Liang
@date: 2026-07-02
@description: Regression coverage for surfacing permanently-failed MessageBus
deliveries to the owner's inbox.

Upstream report: NetMindAI-Open/NarraNexus#52 — when the agent's LLM provider
is misconfigured (e.g. a broken OpenAI key), every `_invoke_runtime` call for
an incoming IM/bus message raises. `MessageBusTrigger._handle_channel_batch`
already records the failure via `LocalMessageBus.record_failure`, but
`LocalMessageBus.get_pending_messages` permanently filters a message out once
`failure_count >= 3` (poison-message guard). Before this change nothing told
the owner: the message just vanished from the queue with zero signal.

Fix: once a message's failure_count reaches the poison threshold, write an
inbox notice (via the same `InboxRepository` path `_write_to_inbox` already
uses) naming the channel and giving a provider/credential-specific hint when
the error looks like one. De-duplicated per (agent_id, error category) with a
cooldown window so a burst of failures sharing one root cause doesn't spam
the inbox with a row per message.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger
from xyz_agent_context.message_bus.schemas import BusMessage
from xyz_agent_context.schema.inbox_schema import InboxMessageType


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "xyz_agent_context.utils.db_factory.get_db_client", _async_db
    )


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert(
        "agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner}
    )


def _boom(error_message: str):
    async def _raise(*args, **kwargs):
        raise RuntimeError(error_message)

    return _raise


@pytest.mark.asyncio
async def test_third_failure_writes_inbox_notification_with_reason(
    db_client, monkeypatch
):
    """Failing the SAME message 3x must write exactly one inbox notice that
    names the channel and hints at a provider/credential problem."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(
        trigger, "_invoke_runtime", _boom("OpenAI API key invalid (401 Unauthorized)")
    )

    msg = BusMessage(
        message_id="m1", channel_id="ch1", from_agent="peer", content="hi"
    )

    for _ in range(3):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg], msg, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["message_type"] == InboxMessageType.SYSTEM_NOTICE.value
    assert "ch1" in row["content"]
    lower = row["content"].lower()
    assert "provider" in lower or "api key" in lower


@pytest.mark.asyncio
async def test_first_and_second_failure_do_not_notify(db_client, monkeypatch):
    """Failures below the poison threshold (< 3) must NOT write to inbox —
    the message can still succeed on a later retry."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("transient network error"))

    msg = BusMessage(
        message_id="m2", channel_id="ch1", from_agent="peer", content="hi"
    )

    for _ in range(2):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg], msg, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert rows == []


@pytest.mark.asyncio
async def test_cooldown_suppresses_duplicate_notification_same_category(
    db_client, monkeypatch
):
    """Two different messages both hitting the poison threshold for the SAME
    error category, back to back, must only produce ONE inbox notice — the
    cooldown window prevents spamming the owner for a single root cause."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(
        trigger, "_invoke_runtime", _boom("OpenAI API key invalid (401 Unauthorized)")
    )

    msg1 = BusMessage(
        message_id="m3", channel_id="ch1", from_agent="peer", content="hi"
    )
    msg2 = BusMessage(
        message_id="m4", channel_id="ch1", from_agent="peer", content="yo"
    )

    for msg in (msg1, msg2):
        for _ in range(3):
            await trigger._handle_channel_batch(
                "agent_a", "ch1", [msg], msg, channel_owner="peer"
            )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1, (
        "cooldown should suppress the second message's notification since "
        f"both share the provider_credential category, got {len(rows)} rows"
    )


@pytest.mark.asyncio
async def test_cooldown_does_not_suppress_different_error_category(
    db_client, monkeypatch
):
    """A different, unrelated error category is NOT suppressed by the
    cooldown from a prior notification — cooldown key is per-category."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    msg1 = BusMessage(
        message_id="m5", channel_id="ch1", from_agent="peer", content="hi"
    )
    monkeypatch.setattr(
        trigger, "_invoke_runtime", _boom("OpenAI API key invalid (401 Unauthorized)")
    )
    for _ in range(3):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg1], msg1, channel_owner="peer"
        )

    msg2 = BusMessage(
        message_id="m6", channel_id="ch1", from_agent="peer", content="yo"
    )
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("agent workspace disk full"))
    for _ in range(3):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg2], msg2, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 2, rows


@pytest.mark.asyncio
async def test_generic_error_gets_generic_hint_not_provider_hint(
    db_client, monkeypatch
):
    """A non-credential error must not falsely tell the owner to check their
    provider config."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("agent workspace disk full"))

    msg = BusMessage(
        message_id="m7", channel_id="ch1", from_agent="peer", content="hi"
    )
    for _ in range(3):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg], msg, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1
    assert "provider" not in rows[0]["content"].lower()


@pytest.mark.asyncio
async def test_no_notification_when_agent_has_no_owner(db_client, monkeypatch):
    """If the owner can't be resolved, the notify path must not raise and
    must not write anything (there's no inbox to write to)."""
    _patch_db_factory(monkeypatch, db_client)
    # No agent row seeded at all — _get_agent_owner resolves to "".

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    monkeypatch.setattr(trigger, "_invoke_runtime", _boom("boom"))

    msg = BusMessage(
        message_id="m8", channel_id="ch1", from_agent="peer", content="hi"
    )
    for _ in range(3):
        # Must not raise even though the agent is unknown.
        await trigger._handle_channel_batch(
            "agent_ghost", "ch1", [msg], msg, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {})
    assert rows == []


# ── PR #45 review follow-ups ────────────────────────────────────────────
#
# 1. Cooldown must only be armed AFTER a successful inbox write — arming it
#    up-front means a transient inbox-write failure (DB blip, etc.) silently
#    swallows the notification AND blocks the next 30 minutes of real
#    attempts for the same category.
# 2. The raw exception string must never be echoed verbatim into the owner's
#    inbox — provider error bodies can echo the API key back (e.g. OpenAI's
#    "Incorrect API key provided: sk-..."), so it must be truncated and any
#    secret-looking substrings must be masked before writing.


@pytest.mark.asyncio
async def test_cooldown_not_armed_when_inbox_write_fails(db_client, monkeypatch):
    """A failed write must NOT arm the cooldown — otherwise one transient
    inbox-write error silently suppresses the real notification for the
    next 30 minutes."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    from xyz_agent_context.repository.inbox_repository import InboxRepository

    original_create = InboxRepository.create_message
    calls = {"n": 0}

    async def _flaky_create_message(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db error")
        return await original_create(self, *args, **kwargs)

    monkeypatch.setattr(InboxRepository, "create_message", _flaky_create_message)

    msg1 = BusMessage(message_id="m9", channel_id="ch1", from_agent="peer", content="hi")
    await trigger._notify_permanent_failure(
        agent_id="agent_a",
        channel_id="ch1",
        trigger_message=msg1,
        error="OpenAI API key invalid (401 Unauthorized)",
    )
    # First attempt's write raised → nothing persisted.
    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert rows == []

    msg2 = BusMessage(message_id="m10", channel_id="ch1", from_agent="peer", content="hi")
    await trigger._notify_permanent_failure(
        agent_id="agent_a",
        channel_id="ch1",
        trigger_message=msg2,
        error="OpenAI API key invalid (401 Unauthorized)",
    )
    # Second attempt (same category) must NOT be suppressed by a cooldown
    # that should never have been armed by the failed first attempt.
    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1, (
        "cooldown must only arm after a successful write; a failed first "
        "attempt must not block the retry"
    )
    assert calls["n"] == 2


def test_redact_error_masks_openai_style_secret_key():
    raw = (
        "Incorrect API key provided: sk-abcDEF1234567890ghijK. You can "
        "find your API key at https://platform.openai.com/account/api-keys."
    )
    redacted = MessageBusTrigger._redact_error_for_owner(raw)
    assert "sk-abcDEF1234567890ghijK" not in redacted


def test_redact_error_masks_key_equals_value_pattern():
    raw = "auth rejected, api_key=abcdef0123456789 is invalid"
    redacted = MessageBusTrigger._redact_error_for_owner(raw)
    assert "abcdef0123456789" not in redacted


def test_redact_error_masks_bearer_token():
    raw = "request failed: Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secretpayload"
    redacted = MessageBusTrigger._redact_error_for_owner(raw)
    assert "eyJhbGciOiJIUzI1NiJ9.secretpayload" not in redacted


def test_redact_error_truncates_long_messages():
    raw = "x" * 2000
    redacted = MessageBusTrigger._redact_error_for_owner(raw)
    assert len(redacted) < 600, "must be truncated, not echoed verbatim"


def test_redact_error_leaves_short_benign_messages_untouched():
    raw = "agent workspace disk full"
    assert MessageBusTrigger._redact_error_for_owner(raw) == raw


@pytest.mark.asyncio
async def test_notify_permanent_failure_never_leaks_raw_secret_into_inbox(
    db_client, monkeypatch
):
    """End-to-end: an error string containing a live-looking API key must
    not appear verbatim in the inbox row content."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)

    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    secret = "sk-liveLookingSecretValue1234567890"
    monkeypatch.setattr(
        trigger,
        "_invoke_runtime",
        _boom(f"Incorrect API key provided: {secret}"),
    )

    msg = BusMessage(message_id="m11", channel_id="ch1", from_agent="peer", content="hi")
    for _ in range(3):
        await trigger._handle_channel_batch(
            "agent_a", "ch1", [msg], msg, channel_owner="peer"
        )

    rows = await db_client.get("inbox_table", {"user_id": "user_x"})
    assert len(rows) == 1
    assert secret not in rows[0]["content"]
    # Still classified + hinted as a provider/credential issue.
    assert "provider" in rows[0]["content"].lower()
