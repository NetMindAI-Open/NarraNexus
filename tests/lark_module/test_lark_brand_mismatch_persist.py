"""
@file_name: test_lark_brand_mismatch_persist.py
@author: Rujing Yan
@date: 2026-07-30
@description: The brand_mismatch circuit breaker actually trips — i.e. the
wrong-brand auth_status reaches the DB.

Regression origin: `_subscribe_loop` built its credential manager with
`LarkCredentialManager(self.db)`, but the attribute on the trigger is
`_db` (`db` is the *manager's* own field name). Every brand-mismatch
detection therefore raised AttributeError inside a `except Exception`
that only logged, so:

  - `auth_status` stayed `bot_ready`
  - `get_active_credentials` kept returning the dead credential
  - the watcher kept restarting a subscriber that could never work
  - the frontend re-bind card and the agent prompt never saw the state

The old test coverage (`test_error_translator.py`) only checked that
error 1000040351 renders nice TEXT, which made the path look tested.

These tests drive a REAL `LarkTrigger` against a REAL db_client on
purpose: mocking the manager out would have let the typo pass, since
the bug was in how the trigger reached for the DB, not in what the
manager does with it.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    AUTH_STATUS_BOT_READY,
    AUTH_STATUS_BRAND_MISMATCH,
    LarkCredential,
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module.lark_trigger import (
    LarkTrigger,
    _is_brand_mismatch_error,
)
from xyz_agent_context.channel.channel_audit_events import (
    EVENT_TRANSPORT_DISCONNECTED,
)
from xyz_agent_context.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)

# The real shape of the SDK failure, as it reaches the except-branch.
WRONG_DOMAIN_ERR = (
    "Exception: ws connect failed, code: 1000040351, "
    "msg: Incorrect domain name"
)


def _make_trigger(db_client) -> LarkTrigger:
    """A trigger wired the way `start()` wires it — nothing else."""
    t = LarkTrigger()
    t.running = True
    # Exactly what ChannelTriggerBase.start() sets — see channel_trigger_base
    # lines 506/520. Keeping this faithful is the whole point: the prod bug
    # was in how the trigger reached `_db`.
    t._db = db_client
    t._audit_repo = ChannelTriggerAuditRepository(t.channel_name, db_client)
    return t


async def _save_bound_credential(db_client, agent_id="a1") -> LarkCredential:
    cred = LarkCredential(
        agent_id=agent_id,
        app_id="cli_wrongbrand",
        app_secret_ref="appsecret:cli_wrongbrand",
        brand="lark",  # bound as Lark, App ID actually registered on Feishu
        profile_name=f"agent_{agent_id}",
        auth_status=AUTH_STATUS_BOT_READY,
        is_active=True,
    )
    await LarkCredentialManager(db_client).save_credential(cred)
    return cred


async def _audit_details(db_client) -> dict:
    rows = await db_client.get("channel_trigger_audit", {"channel": "lark"})
    disconnects = [
        r for r in rows if r["event_type"] == EVENT_TRANSPORT_DISCONNECTED
    ]
    assert len(disconnects) == 1
    return json.loads(disconnects[0]["details"])


# ── The write itself ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brand_mismatch_reaches_the_database(db_client):
    """auth_status=brand_mismatch is durably stored, not just logged."""
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2)

    stored = await LarkCredentialManager(db_client).get_credential(cred.agent_id)
    assert stored is not None
    assert stored.auth_status == AUTH_STATUS_BRAND_MISMATCH


@pytest.mark.asyncio
async def test_breaker_removes_credential_from_the_watcher_set(db_client):
    """The point of the write: the watcher must stop restarting this one.

    `load_active_credentials` is exactly what `_credential_watcher` polls,
    so this asserts the user-visible consequence (hot restart loop stops)
    rather than just the column value.
    """
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    assert [c.agent_id for c in await t.load_active_credentials()] == ["a1"]

    await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2)

    assert await t.load_active_credentials() == []


@pytest.mark.asyncio
async def test_credential_stays_active_so_the_ui_can_still_show_it(db_client):
    """brand_mismatch must NOT deactivate the row.

    The frontend re-bind card keys off a credential that still exists and
    is still `is_active`; flipping is_active would hide the binding the
    user has to fix.
    """
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2)

    stored = await LarkCredentialManager(db_client).get_credential(cred.agent_id)
    assert stored is not None
    assert stored.is_active is True
    assert stored.app_id == "cli_wrongbrand"


# ── Audit trail (incident lessons #3/#5: DB traces over log greps) ────


@pytest.mark.asyncio
async def test_audit_row_records_that_the_breaker_tripped(db_client):
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2)

    details = await _audit_details(db_client)
    assert details["auth_status_set"] == AUTH_STATUS_BRAND_MISMATCH
    assert details["persist_error"] == ""
    # Zero, not a backoff value — this path deliberately does not retry.
    assert details["next_backoff_seconds"] == 0
    assert "1000040351" in details["error"]


@pytest.mark.asyncio
async def test_persist_failure_is_non_fatal_but_visible_in_audit(
    db_client, monkeypatch
):
    """A DB hiccup must not kill the subscriber — but must not go silent.

    This is the case that actually happened in prod (as an AttributeError).
    Back then the only trace was a log line, so the audit trail showed a
    tripped breaker that had not tripped. Now `auth_status_set` is empty
    and `persist_error` names the failure.
    """
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    async def _boom(self, agent_id, status):
        raise RuntimeError("db down")

    monkeypatch.setattr(LarkCredentialManager, "update_auth_status", _boom)

    # Must not raise
    await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2)

    details = await _audit_details(db_client)
    assert details["auth_status_set"] == ""
    assert "RuntimeError" in details["persist_error"]
    assert "db down" in details["persist_error"]


# ── Detection predicate ───────────────────────────────────────────────


def test_detects_wrong_domain_by_error_code():
    assert _is_brand_mismatch_error(WRONG_DOMAIN_ERR)


def test_detects_wrong_domain_by_message_when_code_absent():
    assert _is_brand_mismatch_error("RuntimeError: Incorrect domain name")


def test_ordinary_disconnect_is_not_a_brand_mismatch():
    """These must keep their backoff/reconnect path — tripping the breaker
    on a transient network blip would mute a perfectly good bot."""
    assert not _is_brand_mismatch_error("ConnectionResetError: [Errno 54]")
    assert not _is_brand_mismatch_error("Exception: code: 1000040350, msg: x")
    assert not _is_brand_mismatch_error("TimeoutError: ws handshake timed out")
    assert not _is_brand_mismatch_error("")


@pytest.mark.asyncio
async def test_handler_reports_whether_the_breaker_actually_closed(
    db_client, monkeypatch
):
    """The caller's skip-backoff shortcut is only sound when the status
    landed in the DB — the handler must SAY whether it did (review round on
    PR #202: a failed write left auth_status=bot_ready, so the watcher
    restarted the subscriber every poll tick, i.e. the exact hot-restart
    loop this breaker exists to stop, just gated on a write failure)."""
    t = _make_trigger(db_client)
    cred = await _save_bound_credential(db_client)

    assert await t._handle_brand_mismatch(cred, WRONG_DOMAIN_ERR, ran_seconds=1.2) is True

    async def _boom(self, agent_id, status):
        raise RuntimeError("db down")

    monkeypatch.setattr(LarkCredentialManager, "update_auth_status", _boom)
    cred2 = await _save_bound_credential(db_client, agent_id="a2")
    assert (
        await t._handle_brand_mismatch(cred2, WRONG_DOMAIN_ERR, ran_seconds=1.2)
        is False
    )
