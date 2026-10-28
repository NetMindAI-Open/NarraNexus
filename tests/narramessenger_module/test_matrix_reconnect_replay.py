"""
@file_name: test_matrix_reconnect_replay.py
@date: 2026-10-14
@description: MatrixTrigger reconnect must RESUME, not replay.

Production audit (30 days, channel_trigger_audit): 2011 ingress_dropped_historic
rows were only 186 distinct messages, repeated up to 35x — 182 of them had been
processed live. Two compounding causes, both locked here:

  A. ``_subscribe_loop`` reused the credential captured at subscriber start, so
     every reconnect restarted /sync from the cursor of that moment (days old)
     instead of the cursor persisted on every sync.
  B. The base bumped ONE channel-wide historic baseline on every reconnect, so
     the replay (and any genuinely unprocessed backlog) older than baseline-5min
     was dropped as "historic" before the DB dedup layer saw it.

Everything here drives the REAL ``_subscribe_loop`` + ``MatrixTrigger.connect``
+ ``_dedup_and_handle`` with only the matrix-nio client faked.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from narranexus.platform.channel import channel_trigger_base as base_mod
from narranexus.platform.channel.channel_dedup_store import ChannelDedupStore
from narranexus.platform.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)
from narranexus.platform.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from narranexus_plugins.narramessenger_module import matrix_trigger as mt
from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)

ROOM = "!room:h"
HOUR_MS = 60 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Resp(SimpleNamespace):
    """Stands in for nio.SyncResponse (connect() isinstance-checks it)."""


def _batch(next_batch: str, *events: dict) -> _Resp:
    timeline = SimpleNamespace(events=[SimpleNamespace(raw=e) for e in events])
    room = SimpleNamespace(state=SimpleNamespace(events=[]), timeline=timeline)
    return _Resp(
        rooms=SimpleNamespace(invite={}, join={ROOM: room} if events else {}),
        next_batch=next_batch,
    )


def _event(event_id: str, server_ts: int) -> dict:
    return {
        "kind": "m.room.message.text",
        "event_id": event_id,
        "room_id": ROOM,
        "sender_id": "@human:h",
        "body": f"body of {event_id}",
        "server_ts": server_ts,
    }


class _Script:
    """Scripted /sync: each item is a response or an Exception to raise.
    ``since`` of every call is recorded; the trigger is stopped on the first
    sync AFTER the script is exhausted, so ``_subscribe_loop`` ends
    deterministically once every scripted batch was consumed."""

    def __init__(self, trigger, items, gates=None):
        self.trigger = trigger
        self.items = list(items)
        self.since: list = []
        # sync-call index -> Event awaited before that call is served
        self.gates = gates or {}


def _install_fake_client(monkeypatch, script: _Script) -> None:
    class _FakeClient:
        def __init__(self, **_kw):
            self.device_id = None
            self.access_token = None
            self.user_id = None

        async def sync(self, timeout=None, since=None, full_state=False):
            script.since.append(since)
            gate = script.gates.get(len(script.since) - 1)
            if gate is not None:
                await gate.wait()
            if not script.items:
                # Script exhausted: every scripted batch has been fully
                # consumed by the base loop. Stop the trigger and idle.
                script.trigger.running = False
                return _batch("end")
            item = script.items.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def join(self, _room):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(mt, "AsyncClient", _FakeClient)
    monkeypatch.setattr(mt, "SyncResponse", _Resp)


def _cred(agent_id: str, cursor: str) -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id=agent_id,
        bearer_token="bt",
        matrix_homeserver_url="https://h",
        matrix_user_id=f"@agent-{agent_id}:h",
        matrix_access_token="at",
        matrix_since_token=cursor,
    )


@pytest.fixture
async def harness(db_client, monkeypatch):
    trigger = mt.MatrixTrigger()
    trigger._db = db_client
    trigger.running = True
    trigger._dedup_store = ChannelDedupStore(
        channel=trigger.channel_name,
        repo=ChannelSeenMessageRepository(trigger.channel_name, db_client),
    )
    trigger._dedup_store.update_baseline(_now_ms())  # process startup
    trigger._audit_repo = ChannelTriggerAuditRepository(trigger.channel_name, db_client)
    trigger._wrap_event = lambda *, event, room_id, credential: event.raw

    real_sleep = asyncio.sleep

    async def _fast_sleep(_s):
        await real_sleep(0)

    monkeypatch.setattr(base_mod.asyncio, "sleep", _fast_sleep)
    return SimpleNamespace(trigger=trigger, db=db_client, monkeypatch=monkeypatch)


async def _run(h, cred: NarramessengerCredential, items, *, upsert: bool = True) -> _Script:
    """Persist ``cred`` and drive the real subscribe loop through ``items``.

    ``upsert=False`` re-uses whatever the DB already holds (a re-bind would
    overwrite the cursor, which is not what a plain re-enable does)."""
    if upsert:
        await NarramessengerCredentialManager(h.db).upsert(cred)
    h.trigger.running = True
    script = _Script(h.trigger, items)
    _install_fake_client(h.monkeypatch, script)
    await asyncio.wait_for(h.trigger._subscribe_loop(cred), timeout=10)
    return script


def _queued_ids(trigger) -> list[str]:
    ids = []
    while not trigger._task_queue.empty():
        _cred_, msg = trigger._task_queue.get_nowait()
        ids.append(msg.message_id)
    return ids


async def _audit_rows(h, event_type: str) -> list[dict]:
    rows = await h.db.get("channel_trigger_audit", {"channel": "narramessenger"})
    return [r for r in rows if r["event_type"] == event_type]


@pytest.mark.asyncio
async def test_reconnect_resumes_from_the_persisted_cursor_not_the_stale_credential(harness):
    cred = _cred("agent_a", cursor="s0")
    script = await _run(harness, cred, [
        _batch("s1"),
        _batch("s2"),
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s3"),
    ])
    # sync #1 starts at the bind-time cursor, sync #4 (after the transient
    # failure) must resume at the cursor the loop last persisted — NOT "s0".
    assert script.since[0] == "s0"
    assert script.since[3] == "s2"


@pytest.mark.asyncio
async def test_unprocessed_backlog_older_than_the_buffer_is_processed_after_reconnect(harness):
    old = _event("$never_seen", _now_ms() - HOUR_MS)
    await _run(harness, _cred("agent_a", cursor="s0"), [
        _batch("s1"),
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s2", old),
    ])
    assert _queued_ids(harness.trigger) == ["$never_seen"]
    assert await _audit_rows(harness, "ingress_dropped_historic") == []


@pytest.mark.asyncio
async def test_replayed_already_processed_event_is_dropped_by_the_db_layer(harness):
    old = _event("$already_done", _now_ms() - HOUR_MS)
    # Processed live by an earlier subscriber lifetime: the DB gate knows it.
    seen = ChannelSeenMessageRepository("narramessenger", harness.db)
    assert await seen.mark_seen("agent_a:$already_done") is True

    await _run(harness, _cred("agent_a", cursor="s0"), [
        _batch("s1"),
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s2", old),
    ])
    assert _queued_ids(harness.trigger) == []
    dedup_rows = await _audit_rows(harness, "ingress_dropped_dedup")
    assert [r["message_id"] for r in dedup_rows] == ["$already_done"]
    assert await _audit_rows(harness, "ingress_dropped_historic") == []


@pytest.mark.asyncio
async def test_a_replayed_event_is_processed_exactly_once_across_reconnects(harness):
    old = _event("$once", _now_ms() - HOUR_MS)
    await _run(harness, _cred("agent_a", cursor="s0"), [
        _batch("s1", old),
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s2", old),  # server re-delivers the same batch (at-least-once)
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s3", old),
    ])
    assert _queued_ids(harness.trigger) == ["$once"]


@pytest.mark.asyncio
async def test_first_sync_without_a_cursor_still_drops_old_room_history(harness):
    stale = _event("$history", _now_ms() - HOUR_MS)
    fresh = _event("$fresh", _now_ms() - 5_000)
    await _run(harness, _cred("agent_a", cursor=""), [_batch("s1", stale, fresh)])
    assert _queued_ids(harness.trigger) == ["$fresh"]
    historic = await _audit_rows(harness, "ingress_dropped_historic")
    assert [r["message_id"] for r in historic] == ["$history"]
    assert "age_min" in historic[0]["details"]


@pytest.mark.asyncio
async def test_another_agent_connecting_without_a_cursor_does_not_filter_this_agent(harness):
    """B is ALREADY connected (resumed) when A cold-binds; B then receives an
    hour-old unprocessed event. Per-agent isolation is what keeps B's filter
    where B's own session put it."""
    backlog = _event("$b_backlog", _now_ms() - HOUR_MS)
    gate = asyncio.Event()
    cred_b = _cred("agent_b", cursor="b0")
    await NarramessengerCredentialManager(harness.db).upsert(cred_b)
    b_script = _Script(harness.trigger, [_batch("b1"), _batch("b2", backlog)], gates={1: gate})
    _install_fake_client(harness.monkeypatch, b_script)
    task_b = asyncio.create_task(harness.trigger._subscribe_loop(cred_b))
    while len(b_script.since) < 2:  # B served sync #1 and is parked on the gate
        await asyncio.sleep(0)

    await _run(harness, _cred("agent_a", cursor=""), [_batch("a1")])  # A cold-binds meanwhile
    harness.trigger.running = True  # A's script end stopped the shared trigger
    gate.set()
    await asyncio.wait_for(task_b, timeout=10)
    assert _queued_ids(harness.trigger) == ["$b_backlog"]


async def _toggle(h, agent_id: str, enabled: bool) -> None:
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    assert await GenericCredentialStore(h.db).set_enabled("narramessenger", agent_id, enabled)


async def _assert_reenabled_run_drops_history(h, cred) -> None:
    stale = _event("$sent_while_off", _now_ms() - 3 * 24 * HOUR_MS)
    fresh = _event("$fresh_after_on", _now_ms() - 2_000)
    script = await _run(h, cred, [_batch("s2", stale, fresh)], upsert=False)
    assert script.since[0] is None  # cursor-less first sync
    assert _queued_ids(h.trigger) == ["$fresh_after_on"]
    historic = await _audit_rows(h, "ingress_dropped_historic")
    assert [r["message_id"] for r in historic] == ["$sent_while_off"]


@pytest.mark.asyncio
async def test_disable_then_enable_does_not_answer_what_was_sent_while_off(harness):
    """The user toggle (set-active route -> GenericCredentialStore.set_enabled)
    keeps the row; without clearing the cursor the re-enabled subscriber would
    resume from it with no historic filter and answer days-old messages."""
    cred = _cred("agent_a", cursor="s0")
    await _run(harness, cred, [_batch("s1")])
    assert (await NarramessengerCredentialManager(harness.db).get("agent_a")).matrix_since_token == "end"  # the live cursor

    await _toggle(harness, "agent_a", False)
    await _toggle(harness, "agent_a", True)
    await _assert_reenabled_run_drops_history(harness, cred)


@pytest.mark.asyncio
async def test_a_bundle_imported_binding_does_not_answer_backlog_after_activation(harness):
    """Import lands every binding disabled (``upsert(enabled=False)``) carrying
    the exported public view INCLUDING the source's matrix_since_token. The row
    never passed through set_enabled(False)."""
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    cred = _cred("agent_a", cursor="source_env_cursor")
    values = {k: v for k, v in cred.to_raw_dict().items()
              if k not in ("agent_id", "enabled", "created_at", "updated_at")}
    await GenericCredentialStore(harness.db).upsert("narramessenger", "agent_a", values, enabled=False)
    await _toggle(harness, "agent_a", True)
    await _assert_reenabled_run_drops_history(harness, cred)


@pytest.mark.asyncio
async def test_a_late_cursor_write_after_the_toggle_is_cleared_at_re_enable(harness):
    """The watcher stops a subscriber up to one poll after the toggle; the sync
    finishing in that window writes the cursor onto the DISABLED row."""
    cred = _cred("agent_a", cursor="s0")
    await _run(harness, cred, [_batch("s1")])
    await _toggle(harness, "agent_a", False)
    mgr = NarramessengerCredentialManager(harness.db)
    await mgr.update_since_token("agent_a", "s_late")
    assert (await mgr.get("agent_a")).matrix_since_token == "s_late"
    await _toggle(harness, "agent_a", True)
    await _assert_reenabled_run_drops_history(harness, cred)


@pytest.mark.asyncio
async def test_plain_reconnect_without_a_toggle_still_resumes_and_processes_backlog(harness):
    backlog = _event("$backlog", _now_ms() - 2 * HOUR_MS)
    script = await _run(harness, _cred("agent_a", cursor="s0"), [
        _batch("s1"),
        RuntimeError("Matrix /sync failed: unknown error"),
        _batch("s2", backlog),
    ])
    assert script.since[2] == "s1"
    assert _queued_ids(harness.trigger) == ["$backlog"]
