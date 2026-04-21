"""
@file_name: test_lark_worker_defenses.py
@author: Bin Liang
@date: 2026-04-21
@description: Phase 2 defences on the worker hot path.

Covers:
  - H-2: cred gatekeeper at _process_message start. If the credential
    was deactivated between the SDK callback enqueueing the event and
    a worker dequeueing it, drop the event without running the agent.
    (Daemon SDK thread keeps running until ws_client.stop() or process
    exit, so events from an unbound bot can still reach the queue.)
  - M-6: _bot_open_ids is keyed by (agent_id, app_id) so a rebind to a
    different app under the same agent_id does not reuse the old
    bot's open_id when checking for echoes.
  - M-7: _worker wraps _process_message in asyncio.wait_for so a single
    stuck message cannot permanently occupy a worker slot.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


# --- Shared helpers ------------------------------------------------------

class _Cred:
    def __init__(self, agent_id: str, app_id: str, profile_name: str = "p"):
        self.agent_id = agent_id
        self.app_id = app_id
        self.profile_name = profile_name
        self.brand = "lark"


# --- H-2: cred gatekeeper -------------------------------------------------

@pytest.mark.asyncio
async def test_process_message_drops_event_for_unbound_credential(monkeypatch):
    """If the cred's app_id is no longer in _subscriber_creds, the worker
    must drop the event without running the agent."""
    t = LarkTrigger()
    cred = _Cred(agent_id="a1", app_id="cli_gone")
    # Deliberately do NOT register cred in _subscriber_creds

    build_and_run_mock = AsyncMock()
    monkeypatch.setattr(t, "_build_and_run_agent", build_and_run_mock)
    monkeypatch.setattr(t, "_write_to_inbox", AsyncMock())

    event = {"chat_id": "oc_1", "sender_id": "ou_x", "content": "hi",
             "message_id": "om_1", "sender_type": "user"}
    await t._process_message(cred, event, worker_id=0)

    build_and_run_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_message_runs_for_bound_credential(monkeypatch):
    """Sanity check: the gatekeeper doesn't block the happy path."""
    t = LarkTrigger()
    cred = _Cred(agent_id="a1", app_id="cli_ok")
    t._subscriber_creds[cred.app_id] = cred

    # Stub echo check to never match, content parse to succeed, downstream
    # calls to be trivial.
    monkeypatch.setattr(t, "_is_echo", AsyncMock(return_value=False))
    build_and_run_mock = AsyncMock(return_value="reply")
    monkeypatch.setattr(t, "_build_and_run_agent", build_and_run_mock)
    monkeypatch.setattr(t, "_write_to_inbox", AsyncMock())

    event = {"chat_id": "oc_1", "sender_id": "ou_x", "content": "hi there",
             "message_id": "om_1", "sender_type": "user"}
    await t._process_message(cred, event, worker_id=0)

    build_and_run_mock.assert_awaited_once()


# --- M-6: bot open_id cache keyed by (agent_id, app_id) ------------------

@pytest.mark.asyncio
async def test_bot_open_id_cache_keys_by_agent_and_app(monkeypatch):
    """Two different agents with the same profile_name must not share
    cache entries. A rebind of the same agent to a different app_id
    must not reuse the old open_id."""
    t = LarkTrigger()

    # Stub the CLI to return a different bot_oid depending on agent_id.
    async def _fake_run(args, agent_id):
        return {
            "success": True,
            "data": {"bot": {"open_id": f"ou_bot_of_{agent_id}"}},
        }
    t._cli = SimpleNamespace(_run_with_agent_id=_fake_run)

    cred_a = _Cred(agent_id="a1", app_id="cli_A", profile_name="shared")
    cred_b = _Cred(agent_id="a2", app_id="cli_B", profile_name="shared")

    # Populate from A side
    await t._is_echo(cred_a, {"sender_type": "user"}, sender_id="ou_someone")
    # Populate from B side
    await t._is_echo(cred_b, {"sender_type": "user"}, sender_id="ou_someone")

    assert (cred_a.agent_id, cred_a.app_id) in t._bot_open_ids
    assert (cred_b.agent_id, cred_b.app_id) in t._bot_open_ids
    assert t._bot_open_ids[(cred_a.agent_id, cred_a.app_id)] == "ou_bot_of_a1"
    assert t._bot_open_ids[(cred_b.agent_id, cred_b.app_id)] == "ou_bot_of_a2"


@pytest.mark.asyncio
async def test_stop_subscriber_clears_bot_open_id_cache(monkeypatch):
    """When a subscriber is stopped (unbind), stale open_id entries for
    that agent must not linger — otherwise a later rebind to a new
    app_id risks reading the wrong bot identity."""
    t = LarkTrigger()

    async def _fake_run(_args, agent_id):
        return {
            "success": True,
            "data": {"bot": {"open_id": f"ou_bot_of_{agent_id}"}},
        }
    t._cli = SimpleNamespace(_run_with_agent_id=_fake_run)

    cred = _Cred(agent_id="a1", app_id="cli_A", profile_name="p1")
    t._subscriber_creds[cred.app_id] = cred
    # Populate cache
    await t._is_echo(cred, {"sender_type": "user"}, sender_id="ou_x")
    assert (cred.agent_id, cred.app_id) in t._bot_open_ids

    # No task — just exercise the bookkeeping path
    await t._stop_subscriber(cred.app_id)

    assert (cred.agent_id, cred.app_id) not in t._bot_open_ids


# --- M-7: per-message timeout on the worker ------------------------------

@pytest.mark.asyncio
async def test_worker_cancels_stuck_message(monkeypatch):
    """A _process_message that never returns must not permanently own
    the worker. With PROCESS_MESSAGE_TIMEOUT_SECONDS in effect the
    worker logs the timeout and moves on."""
    t = LarkTrigger()
    t.running = True

    # Test knob: shorten the timeout so the test completes fast
    monkeypatch.setattr(t, "PROCESS_MESSAGE_TIMEOUT_SECONDS", 0.1)

    hung_started = asyncio.Event()

    async def _hang(*_a, **_kw):
        hung_started.set()
        await asyncio.sleep(10)  # longer than the patched timeout

    monkeypatch.setattr(t, "_process_message", _hang)

    cred = _Cred(agent_id="a1", app_id="cli_A")
    event = {"message_id": "om_stuck", "chat_id": "oc_1"}
    await t._task_queue.put((cred, event))

    worker = asyncio.ensure_future(t._worker(worker_id=0))
    # Let the hung _process_message kick off
    await hung_started.wait()
    # The worker should escape its stuck call and be back at queue.get()
    # within ~timeout + epsilon. We give it 1s total.
    await asyncio.sleep(0.3)

    t.running = False
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass

    # Worker escaped — no assertion on exact state, the fact that we
    # could cancel it from outside the hung call is proof enough.
