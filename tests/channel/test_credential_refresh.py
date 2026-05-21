"""
@file_name: test_credential_refresh.py
@author: Bin Liang
@date: 2026-05-21
@description: Mid-session credential refresh for long-lived subscribers.

Regression (debug/20260521-agent-running-halo, found while checking Lark
sender-name resolution on EC2): a subscriber captures its credential once
at connect time and the watcher only ever wrote the credential cache when
*starting* a new subscriber. When the owner completes the three-click user
authorization mid-session, the running subscriber kept using the stale
pre-auth credential — so resolve_sender_name's `user_oauth_ok()` gate kept
returning False and every sender stayed "Unknown" until the subscriber
restarted.

Fix (both in ChannelTriggerBase, so every channel benefits):
  - _credential_watcher refreshes _subscriber_creds[key] to the latest DB
    snapshot every poll, not just when the subscriber is first created.
  - _worker re-resolves the credential from _subscriber_creds at dequeue
    time, so per-message logic (name resolution, context build) sees the
    freshest credential without dropping the transport connection.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

import pytest

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import ParsedMessage


@dataclass
class _MutCredential:
    agent_id: str = "agent_a"
    app_id: str = "bot1"
    permission_state: dict = field(default_factory=dict)


# ── Test 1: worker uses the freshest cached credential, not the dequeued one ──

class _CapturingTrigger(ChannelTriggerBase):
    """Captures the credential its _process_message actually receives."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK

    def __init__(self):
        super().__init__(base_workers=1)
        self.captured = None

    async def load_active_credentials(self):
        return []

    async def connect(self, credential):  # pragma: no cover - unused here
        if False:
            yield {}

    def parse_event(self, raw):  # pragma: no cover - unused here
        return None

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return "x"

    def create_context_builder(self, message, credential, agent_id):
        return None

    async def _process_message(self, credential, message):
        # Record which credential the worker handed us, then halt the loop.
        self.captured = credential
        self.running = False


@pytest.mark.asyncio
async def test_worker_uses_latest_cached_credential():
    trigger = _CapturingTrigger()
    cred_old = _MutCredential(permission_state={})
    cred_new = _MutCredential(permission_state={"user_oauth_completed_at": "2026-05-21T08:07:57Z"})

    key = trigger._subscriber_key(cred_old)
    # Watcher has already refreshed the cache to the post-auth credential.
    trigger._subscriber_creds[key] = cred_new

    msg = ParsedMessage(
        message_id="m1", chat_id="C1", sender_id="u_alice",
        sender_name="", content="hi", timestamp_ms=1,
    )
    # The event was enqueued earlier carrying the stale pre-auth credential.
    await trigger._task_queue.put((cred_old, msg))

    trigger.running = True
    worker = asyncio.create_task(trigger._worker(0))
    try:
        for _ in range(50):
            if trigger.captured is not None:
                break
            await asyncio.sleep(0.02)
    finally:
        trigger.running = False
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    assert trigger.captured is cred_new
    assert trigger.captured.permission_state.get("user_oauth_completed_at")


@pytest.mark.asyncio
async def test_worker_falls_back_when_key_uncached():
    """No cached credential for the key (subscriber stopped) → use the
    dequeued credential. The base _process_message gatekeeper then drops
    it; here we just assert no crash and the dequeued cred is used."""
    trigger = _CapturingTrigger()
    cred = _MutCredential()
    msg = ParsedMessage(
        message_id="m1", chat_id="C1", sender_id="u", sender_name="",
        content="hi", timestamp_ms=1,
    )
    await trigger._task_queue.put((cred, msg))

    trigger.running = True
    worker = asyncio.create_task(trigger._worker(0))
    try:
        for _ in range(50):
            if trigger.captured is not None:
                break
            await asyncio.sleep(0.02)
    finally:
        trigger.running = False
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    assert trigger.captured is cred


# ── Test 2: watcher refreshes the cached credential for a running subscriber ──

class _RefreshTrigger(ChannelTriggerBase):
    """load_active_credentials returns a fresh credential each poll built
    from a mutable permission_state, simulating a DB row that changes
    mid-session."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK
    CREDENTIAL_POLL_INTERVAL_SECONDS = 0.2
    IDLE_POLL_INTERVAL_SECONDS = 0.2

    def __init__(self):
        super().__init__(base_workers=1)
        self.perm_state: dict = {}

    async def load_active_credentials(self):
        return [_MutCredential(permission_state=dict(self.perm_state))]

    async def connect(self, credential):
        # No events; return promptly to simulate a clean disconnect. The
        # subscribe loop will back off; the watcher keeps polling.
        await asyncio.sleep(0.05)
        return
        yield {}  # pragma: no cover - marks this an async generator

    def parse_event(self, raw):  # pragma: no cover - unused
        return None

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return "x"

    def create_context_builder(self, message, credential, agent_id):
        return None


async def _wait_until(predicate, timeout=4.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


@pytest.mark.asyncio
async def test_watcher_refreshes_running_subscriber_credential(db_client):
    trigger = _RefreshTrigger()
    await trigger.start(db_client)
    key = "bot1"
    try:
        # Subscriber comes up with the pre-auth credential.
        assert await _wait_until(lambda: key in trigger._subscriber_creds)
        assert trigger._subscriber_creds[key].permission_state == {}

        # Owner completes the three-click auth — DB row gains the token.
        trigger.perm_state = {"user_oauth_completed_at": "2026-05-21T08:07:57Z"}

        # Next watcher poll must refresh the cached credential in place,
        # without the subscriber having to restart.
        assert await _wait_until(
            lambda: trigger._subscriber_creds[key].permission_state.get(
                "user_oauth_completed_at"
            )
        )
    finally:
        await trigger.stop()
