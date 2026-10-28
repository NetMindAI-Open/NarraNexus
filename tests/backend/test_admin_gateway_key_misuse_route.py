"""
@file_name: test_admin_gateway_key_misuse_route.py
@author: Bin Liang
@date: 2026-08-19
@description: The internal admin gateway-key-misuse endpoint —
POST /api/admin/gateway-key-misuse — the SOLE writer of gateway_key_misuse.

Self-credentialed on the X-Admin-Secret header (same lock as suspend). Covers:
- no / wrong X-Admin-Secret -> 403 (never open); unset secret -> 503
- an authenticated call writes exactly one gateway_key_misuse row with the
  fields it was handed and disposition_status='pending'
- the endpoint records ONLY what it is given: it never parses free-form text for
  attribution — user_id is whatever the caller reverse-resolved
- an unresolved event (user_id=None) is stored as an alert-only row (user_id NULL)
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

SECRET = "test-admin-secret-xyz"
UID = "9f3a1c229f3a1c229f3a1c229f3a1c22"
PATH = "/api/admin/gateway-key-misuse"


def _make_app(db_client, monkeypatch, *, secret=SECRET):
    import backend.routes.admin.gateway_key_misuse as mod

    async def _ret(v):
        return v

    monkeypatch.setattr(mod, "get_db_client", lambda: _ret(db_client))
    monkeypatch.setattr(mod.settings, "admin_secret_key", secret)

    app = FastAPI()
    app.include_router(mod.router)
    return app


async def _post(app, path, json=None, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.post(path, json=json, headers=headers)


# --------------------------- auth wiring ---------------------------

def test_misuse_path_is_auth_exempt():
    """The endpoint is self-credentialed on X-Admin-Secret, so the user-auth
    middleware must let it through (the caller is a machine with no user JWT).
    Same exemption as /api/admin/suspend."""
    from backend.auth import AUTH_EXEMPT_PATHS

    assert PATH in AUTH_EXEMPT_PATHS


# --------------------------- admin-secret gate ---------------------------

@pytest.mark.asyncio
async def test_misuse_requires_admin_secret(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)

    resp = await _post(app, PATH, json={"user_id": UID})

    assert resp.status_code == 403
    # nothing written
    rows = await db_client.get("gateway_key_misuse", {})
    assert rows == []


@pytest.mark.asyncio
async def test_misuse_wrong_secret_rejected(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": UID}, headers={"X-Admin-Secret": "nope"},
    )

    assert resp.status_code == 403
    rows = await db_client.get("gateway_key_misuse", {})
    assert rows == []


@pytest.mark.asyncio
async def test_misuse_secret_not_configured_is_503(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch, secret="")

    resp = await _post(
        app, PATH,
        json={"user_id": UID}, headers={"X-Admin-Secret": "anything"},
    )

    assert resp.status_code == 503


# --------------------------- write behaviour ---------------------------

@pytest.mark.asyncio
async def test_misuse_writes_one_row(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": UID, "run_id": "run_abc", "key_hash": "hh",
              "caller_ip": "1.2.3.4", "caller_ua": "pool/1", "model": "m"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["already"] is False  # fresh insert, not a collapsed retry
    assert isinstance(body["id"], int) and body["id"] >= 1

    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == UID
    assert row["run_id"] == "run_abc"
    assert row["key_hash"] == "hh"
    assert row["caller_ip"] == "1.2.3.4"
    assert row["caller_ua"] == "pool/1"
    assert row["model"] == "m"
    # every event lands as 'pending' — the monitor/dispositioner advances it later
    assert row["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_misuse_unresolved_writes_alert_only_row_without_uid(db_client, monkeypatch):
    """An unresolved event is still recorded — as an alert-only row with user_id
    NULL. We never fabricate an actionable id."""
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": None, "run_id": None, "key_hash": "x",
              "caller_ip": "1.2.3.4", "caller_ua": "ua", "model": "m"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    assert rows[0]["user_id"] is None
    assert rows[0]["disposition_status"] == "pending"


# --------------------------- M3: idempotent retry (no duplicate rows) ---------------------------

@pytest.mark.asyncio
async def test_retry_with_same_hit_at_is_idempotent(db_client, monkeypatch):
    """A write-succeeded-but-response-timed-out retry carries the same
    (key_hash, hit_at); it must collapse to ONE row and return the same id — a
    hard signal is acted on once, so a duplicate row would double-act."""
    app = _make_app(db_client, monkeypatch)
    payload = {
        "user_id": UID,
        "key_hash": "dedup_kh",
        "hit_at": "2026-08-19 10:00:00.000000",
        "caller_ip": "1.2.3.4",
    }

    r1 = await _post(app, PATH, json=payload, headers={"X-Admin-Secret": SECRET})
    r2 = await _post(app, PATH, json=payload, headers={"X-Admin-Secret": SECRET})

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]  # same row
    assert r1.json()["already"] is False  # first write
    assert r2.json()["already"] is True   # collapsed retry — observable to the caller
    rows = await db_client.get("gateway_key_misuse", {"key_hash": "dedup_kh"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_hit_at_z_and_offset_forms_dedup_to_one_event(db_client, monkeypatch):
    """hit_at is normalised to the DATETIME(6) UTC contract before write AND
    reverse-lookup, so a ``Z`` form and its already-normalised form are the SAME
    event: they collapse to one row. Without normalisation they would be two
    distinct strings and two rows."""
    app = _make_app(db_client, monkeypatch)
    base = {"user_id": UID, "key_hash": "znorm_kh", "caller_ip": "1.2.3.4"}

    r1 = await _post(app, PATH, json={**base, "hit_at": "2026-08-19T10:00:00Z"},
                     headers={"X-Admin-Secret": SECRET})
    r2 = await _post(app, PATH, json={**base, "hit_at": "2026-08-19 10:00:00.000000"},
                     headers={"X-Admin-Secret": SECRET})

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["already"] is True
    # Collapsing to one row IS the proof of normalisation: the raw ``Z`` string
    # and the space-separated form are different bytes; only after both are
    # normalised to the same DATETIME(6) literal (for write AND reverse-lookup)
    # can the (key_hash, hit_at) index treat them as the same event.
    rows = await db_client.get("gateway_key_misuse", {"key_hash": "znorm_kh"})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_unparseable_hit_at_still_records_event(db_client, monkeypatch):
    """An illegal hit_at literal would 1292/500 on MySQL's DATETIME(6) and DROP
    the event — but every event MUST land. So an unparseable hit_at is dropped
    (the column default = insert time applies) and the event still records."""
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": UID, "key_hash": "badtime_kh", "hit_at": "not-a-real-datetime"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {"key_hash": "badtime_kh"})
    assert len(rows) == 1
    # the raw illegal literal was NOT stored — it was dropped for the col default
    assert rows[0]["hit_at"] != "not-a-real-datetime"
    assert rows[0]["hit_at"]  # a real timestamp landed (the insert-time default)
    assert rows[0]["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_coarse_date_only_hit_at_is_dropped_not_stored_as_midnight(db_client, monkeypatch):
    """A bare-date hit_at (no time-of-day) parses to a VALID but COARSE literal —
    midnight of that day. Keeping it would collapse distinct events that merely
    share a day onto one (key_hash, hit_at) anchor. So a value with no
    time-of-day is dropped like an unparseable one: the event lands on the
    column-default (insert-time) stamp, NOT on the past midnight literal.

    Uses a date years in the past so the assertion is deterministic: if the guard
    is removed, the stored value is that 2020 midnight (far from now); with the
    guard it is the insert-time default (close to now)."""
    from datetime import timedelta
    from narranexus.platform.utils.timezone import coerce_utc, utc_now

    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": UID, "key_hash": "coarse_kh", "hit_at": "2020-01-15"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {"key_hash": "coarse_kh"})
    assert len(rows) == 1
    stored = coerce_utc(rows[0]["hit_at"])
    assert stored is not None
    # The coarse date was DROPPED (not normalised-and-stored as 2020 midnight):
    # the insert-time default landed instead, so the stamp is close to now.
    assert abs(utc_now() - stored) < timedelta(minutes=5)
    assert rows[0]["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_distinct_hit_at_same_key_are_separate_events(db_client, monkeypatch):
    """Two genuine misuse events on the same key at different times are distinct
    rows — dedup keys on (key_hash, hit_at), not key_hash alone."""
    app = _make_app(db_client, monkeypatch)
    base = {"user_id": UID, "key_hash": "same_kh", "caller_ip": "1.2.3.4"}

    r1 = await _post(app, PATH, json={**base, "hit_at": "2026-08-19 10:00:00.000000"},
                     headers={"X-Admin-Secret": SECRET})
    r2 = await _post(app, PATH, json={**base, "hit_at": "2026-08-19 11:00:00.000000"},
                     headers={"X-Admin-Secret": SECRET})

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]
    rows = await db_client.get("gateway_key_misuse", {"key_hash": "same_kh"})
    assert len(rows) == 2


# --------------------------- C1: server-side clipping (never drop a row) ---------------------------

@pytest.mark.asyncio
async def test_over_length_fields_are_clipped_not_rejected(db_client, monkeypatch):
    """Attacker-influenced fields exceeding their column width are CLIPPED
    server-side, not 422'd. A dropped event = a missed enforcement, so any
    event must land."""
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={
            "user_id": UID,
            "run_id": "r" * 500,       # col 128
            "key_hash": "k" * 900,     # col 256
            "caller_ip": "1" * 300,    # col 64
            "caller_ua": "u" * 5000,   # col 256 — no max_length, must not 422
            "model": "m" * 400,        # col 128
        },
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == UID  # within width, untouched
    assert len(row["run_id"]) == 128
    assert len(row["key_hash"]) == 256
    assert len(row["caller_ip"]) == 64
    assert len(row["caller_ua"]) == 256
    assert len(row["model"]) == 128
    assert row["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_over_length_user_id_becomes_alert_only_null_row(db_client, monkeypatch):
    """user_id is NEVER truncated (a clipped id could collide with a different
    real user). An over-long id is treated as unresolved: the event still lands,
    as an alert-only row with user_id=NULL and disposition 'pending'."""
    app = _make_app(db_client, monkeypatch)

    # 65 chars — just OVER the VARCHAR(64) width, so this catches a threshold
    # left at the old 128 (that would keep 65 chars as an authoritative id).
    resp = await _post(
        app, PATH,
        json={"user_id": "x" * 65, "key_hash": "hh", "caller_ip": "1.2.3.4"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    assert rows[0]["user_id"] is None
    assert rows[0]["key_hash"] == "hh"
    assert rows[0]["disposition_status"] == "pending"


@pytest.mark.asyncio
async def test_user_id_exactly_at_limit_is_kept(db_client, monkeypatch):
    """A user_id of exactly the column width is a real, resolvable id — keep it."""
    app = _make_app(db_client, monkeypatch)
    uid_64 = "u" * 64

    resp = await _post(
        app, PATH,
        json={"user_id": uid_64, "key_hash": "hh"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    assert rows[0]["user_id"] == uid_64


@pytest.mark.asyncio
async def test_field_length_limits_derive_from_schema(db_client, monkeypatch):
    """The route's clip/threshold widths are the schema registry's column widths
    (single source of truth) — not a second hardcoded copy that could drift."""
    from backend.routes.admin.gateway_key_misuse import (
        CALLER_IP_MAX_LEN,
        CALLER_UA_MAX_LEN,
        KEY_HASH_MAX_LEN,
        MODEL_MAX_LEN,
        RUN_ID_MAX_LEN,
        USER_ID_MAX_LEN,
    )
    from narranexus.platform.utils.db.schema_registry import varchar_width

    assert USER_ID_MAX_LEN == varchar_width("gateway_key_misuse", "user_id") == 64
    assert RUN_ID_MAX_LEN == varchar_width("gateway_key_misuse", "run_id")
    assert KEY_HASH_MAX_LEN == varchar_width("gateway_key_misuse", "key_hash") == 256
    assert CALLER_IP_MAX_LEN == varchar_width("gateway_key_misuse", "caller_ip")
    assert CALLER_UA_MAX_LEN == varchar_width("gateway_key_misuse", "caller_ua")
    assert MODEL_MAX_LEN == varchar_width("gateway_key_misuse", "model")


@pytest.mark.asyncio
async def test_misuse_ignores_unknown_fields(db_client, monkeypatch):
    """The endpoint records ONLY the fields it declares. Any text a caller
    (or attacker) tries to smuggle in for attribution is dropped on the floor —
    user_id is the ONLY attribution, and it is whatever the caller resolved."""
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, PATH,
        json={"user_id": UID, "note": "some::VICTIM::run_9",
              "attacker_controlled": "ban-this-guy"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    rows = await db_client.get("gateway_key_misuse", {})
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == UID
    assert "note" not in row
    assert "attacker_controlled" not in row
