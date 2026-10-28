"""
@file_name: test_admin_suspend_route.py
@author: Bin Liang
@date: 2026-08-13
@description: The admin account-suspension mechanism —
POST /api/admin/suspend, POST /api/admin/reinstate,
GET /api/admin/account-state/{user_id}.

A generic, policy-free switch over a user's account state, self-credentialed on
the X-Admin-Secret header (same pattern as migrate-identity). Covers:
- no / wrong X-Admin-Secret -> 403 on every endpoint (never open)
- suspend flips users.status to "banned", writes a ban_audit row, returns
  {suspended, already}
- suspend is idempotent: a second suspend returns already=True and does not
  change the state
- reinstate restores status to "active" and writes a reinstate audit row
- account-state reflects the current status
- opaque reason/evidence_ref are recorded verbatim
- unknown user -> 404
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

SECRET = "test-admin-secret-xyz"
UID = "u_suspend_1"


def _make_app(db_client, monkeypatch, *, secret=SECRET):
    import backend.routes.admin.suspend as mod

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


async def _get(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        return await ac.get(path, headers=headers)


async def _seed_user(db_client, user_id=UID, status="active"):
    await db_client.insert(
        "users", {"user_id": user_id, "user_type": "individual", "status": status}
    )


# --------------------------- admin-secret gate ---------------------------

@pytest.mark.asyncio
async def test_suspend_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(app, "/api/admin/suspend", json={"user_id": UID})

    assert resp.status_code == 403
    # untouched
    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_suspend_wrong_secret_rejected(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": "nope"},
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reinstate_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(app, "/api/admin/reinstate", json={"user_id": UID})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_account_state_requires_admin_secret(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _get(app, f"/api/admin/account-state/{UID}")

    assert resp.status_code == 403


# --------------------------- suspend behaviour ---------------------------

@pytest.mark.asyncio
async def test_suspend_flips_status_and_writes_audit(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID, "reason": "opaque-note", "evidence_ref": "ref-42",
              "actor": "ops-bot"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "suspended": True, "already": False, "jobs_paused": 0, "jobs_pause_error": None,
    }

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "banned"

    audit = await db_client.get("ban_audit", {"user_id": UID})
    assert len(audit) == 1
    assert audit[0]["action"] == "suspend"
    assert audit[0]["reason"] == "opaque-note"
    assert audit[0]["evidence_ref"] == "ref-42"
    assert audit[0]["actor"] == "ops-bot"


@pytest.mark.asyncio
async def test_suspend_is_idempotent(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    first = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )
    second = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert first.json() == {
        "suspended": True, "already": False, "jobs_paused": 0, "jobs_pause_error": None,
    }
    assert second.json() == {
        "suspended": True, "already": True, "jobs_paused": 0, "jobs_pause_error": None,
    }

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "banned"


@pytest.mark.asyncio
async def test_reinstate_restores_active_and_audits(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/reinstate",
        json={"user_id": UID, "actor": "ops-bot"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    assert resp.json() == {"reinstated": True, "jobs_resumed": 0, "jobs_resume_error": None}

    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "active"

    audit = await db_client.get("ban_audit", {"user_id": UID})
    reinstate_rows = [a for a in audit if a["action"] == "reinstate"]
    assert reinstate_rows
    # prev_status records what the reinstate reverted from.
    assert reinstate_rows[0]["prev_status"] == "banned"


@pytest.mark.asyncio
async def test_suspend_records_prev_status(db_client, monkeypatch):
    await _seed_user(db_client)  # active
    app = _make_app(db_client, monkeypatch)

    await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    audit = await db_client.get("ban_audit", {"user_id": UID})
    suspend_rows = [a for a in audit if a["action"] == "suspend"]
    assert suspend_rows
    # Suspend replaced the "active" state — recorded verbatim.
    assert suspend_rows[0]["prev_status"] == "active"


@pytest.mark.parametrize("status", ["blocked", "deleted"])
@pytest.mark.asyncio
async def test_reinstate_only_revives_banned_accounts(db_client, monkeypatch, status):
    """A `blocked` / `deleted` account was NOT suspended by this mechanism, so
    reinstate must refuse (409) and leave the row untouched — it never silently
    flips a pre-existing terminal state to active."""
    await _seed_user(db_client, status=status)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/reinstate",
        json={"user_id": UID, "actor": "ops-bot"},
        headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reinstated"] is False
    assert detail["not_suspended_by_this_mechanism"] is True

    # Row is UNCHANGED.
    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == status

    # The refused attempt is still audited, with prev_status recorded.
    audit = await db_client.get("ban_audit", {"user_id": UID})
    reinstate_rows = [a for a in audit if a["action"] == "reinstate"]
    assert reinstate_rows
    assert reinstate_rows[0]["prev_status"] == status


@pytest.mark.asyncio
async def test_account_state_reflects_status(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch)

    before = await _get(
        app, f"/api/admin/account-state/{UID}",
        headers={"X-Admin-Secret": SECRET},
    )
    assert before.status_code == 200
    assert before.json() == {"user_id": UID, "status": "active"}

    await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    after = await _get(
        app, f"/api/admin/account-state/{UID}",
        headers={"X-Admin-Secret": SECRET},
    )
    assert after.json() == {"user_id": UID, "status": "banned"}


@pytest.mark.asyncio
async def test_suspend_unknown_user_is_404(db_client, monkeypatch):
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": "ghost"}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_secret_not_configured_is_503(db_client, monkeypatch):
    await _seed_user(db_client)
    app = _make_app(db_client, monkeypatch, secret="")

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": "anything"},
    )

    assert resp.status_code == 503


# --------------------- B-13: suspend pauses the user's jobs ---------------------
# A banned user's scheduled jobs must stop re-firing immediately, in the same
# request that flips `users.status` — otherwise the job keeps hammering the
# provider with `Key is blocked` 401s until the next poll cycle happens to
# notice (and only then because job_trigger separately checks ban state).

SCHEDULED_TRIGGER = '{"cron":"0 8 * * *","timezone":"Asia/Shanghai"}'


async def _seed_job(db_client, job_id, user_id, status="active", related_entity_id=None,
                    paused_reason=None, trigger_config=SCHEDULED_TRIGGER, job_type="scheduled"):
    row = {
        "job_id": job_id,
        "instance_id": f"ins_{job_id}",
        "agent_id": "agent_1",
        "user_id": user_id,
        "title": "t", "description": "d", "payload": "p",
        "job_type": job_type,
        "trigger_config": trigger_config,
        "status": status,
        "notification_method": "inbox",
        # A stale fire from before the suspension: a blind ACTIVE flip would
        # make get_due_jobs replay it immediately.
        "next_run_time": "2020-01-01 00:00:00",
    }
    if related_entity_id:
        row["related_entity_id"] = related_entity_id
    if paused_reason is not None:
        row["paused_reason"] = paused_reason
    await db_client.insert("instance_jobs", row)


@pytest.mark.asyncio
async def test_suspend_pauses_the_users_active_jobs(db_client, monkeypatch):
    await _seed_user(db_client)
    await _seed_job(db_client, "job_1", UID, status="active")
    await _seed_job(db_client, "job_2", UID, status="pending")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    assert resp.json()["jobs_paused"] == 2
    for job_id in ("job_1", "job_2"):
        row = await db_client.get_one("instance_jobs", {"job_id": job_id})
        assert row["status"] == "paused"
        assert row["paused_reason"] == "banned"


@pytest.mark.asyncio
async def test_suspend_does_not_touch_terminal_jobs(db_client, monkeypatch):
    """Completed / cancelled / failed jobs never run again regardless — suspend
    must not rewrite their terminal status."""
    await _seed_user(db_client)
    await _seed_job(db_client, "job_done", UID, status="completed")
    await _seed_job(db_client, "job_cancelled", UID, status="cancelled")
    app = _make_app(db_client, monkeypatch)

    await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    row = await db_client.get_one("instance_jobs", {"job_id": "job_done"})
    assert row["status"] == "completed"
    row = await db_client.get_one("instance_jobs", {"job_id": "job_cancelled"})
    assert row["status"] == "cancelled"


@pytest.mark.asyncio
async def test_suspend_idempotent_second_call_leaves_already_paused_jobs(db_client, monkeypatch):
    await _seed_user(db_client)
    await _seed_job(db_client, "job_1", UID, status="active")
    app = _make_app(db_client, monkeypatch)

    await _post(app, "/api/admin/suspend", json={"user_id": UID}, headers={"X-Admin-Secret": SECRET})
    second = await _post(app, "/api/admin/suspend", json={"user_id": UID}, headers={"X-Admin-Secret": SECRET})

    assert second.json() == {
        "suspended": True, "already": True, "jobs_paused": 0, "jobs_pause_error": None,
    }
    row = await db_client.get_one("instance_jobs", {"job_id": "job_1"})
    assert row["status"] == "paused"
    assert row["paused_reason"] == "banned"


# ---- review I2: selection is by EXECUTION principal, same as the poller ----

@pytest.mark.asyncio
async def test_suspend_pauses_jobs_that_execute_as_the_suspended_user(db_client, monkeypatch):
    """Owned by someone else, but `related_entity_id` = the suspended user:
    the poller would refuse to run it as that principal, so suspend pauses it."""
    await _seed_user(db_client)
    await _seed_user(db_client, user_id="u_owner_ok")
    await _seed_job(db_client, "job_delegated", "u_owner_ok", status="active", related_entity_id=UID)
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.json()["jobs_paused"] == 1
    row = await db_client.get_one("instance_jobs", {"job_id": "job_delegated"})
    assert row["status"] == "paused"
    assert row["paused_reason"] == "banned"


@pytest.mark.asyncio
async def test_suspend_leaves_jobs_that_execute_as_another_principal(db_client, monkeypatch):
    """Owned by the suspended user but run as someone who may transact — the
    poller judges the execution principal, so suspend must not contradict it."""
    await _seed_user(db_client)
    await _seed_user(db_client, user_id="u_principal_ok")
    await _seed_job(db_client, "job_runs_as_other", UID, status="active", related_entity_id="u_principal_ok")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.json()["jobs_paused"] == 0
    row = await db_client.get_one("instance_jobs", {"job_id": "job_runs_as_other"})
    assert row["status"] == "active"


# ---- review I3: no 500-row ceiling ----

@pytest.mark.asyncio
async def test_suspend_pauses_more_than_five_hundred_jobs(db_client, monkeypatch):
    await _seed_user(db_client)
    for i in range(520):
        await _seed_job(db_client, f"job_bulk_{i}", UID, status="active")
    app = _make_app(db_client, monkeypatch)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.json()["jobs_paused"] == 520
    rows = await db_client.execute(
        "SELECT COUNT(*) AS n FROM instance_jobs WHERE user_id = %s AND status = %s",
        (UID, "active"), fetch=True,
    )
    assert rows[0]["n"] == 0


# ---- review I4: the pause is best-effort and comes after audit + cache ----

@pytest.mark.asyncio
async def test_suspend_survives_a_job_pause_failure_and_still_audits(db_client, monkeypatch):
    from narranexus.platform.repository.job_repository import JobRepository

    await _seed_user(db_client)
    await _seed_job(db_client, "job_1", UID, status="active")
    app = _make_app(db_client, monkeypatch)

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("instance_jobs unavailable")

    monkeypatch.setattr(JobRepository, "pause_jobs_for_execution_principal", _boom)

    resp = await _post(
        app, "/api/admin/suspend",
        json={"user_id": UID, "actor": "ops-bot"}, headers={"X-Admin-Secret": SECRET},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["suspended"] is True
    assert body["jobs_paused"] == 0
    assert "instance_jobs unavailable" in body["jobs_pause_error"]
    # The account IS suspended and the audit row IS written.
    row = await db_client.get_one("users", {"user_id": UID})
    assert row["status"] == "banned"
    audit = await db_client.get("ban_audit", {"user_id": UID})
    assert [a["action"] for a in audit] == ["suspend"]
    # The job is left for the poller's own account gate.
    job = await db_client.get_one("instance_jobs", {"job_id": "job_1"})
    assert job["status"] == "active"



# ---- review r2 I-B: reinstate undoes exactly what suspend paused ----

async def _reinstate(app, uid=UID):
    return await _post(
        app, "/api/admin/reinstate", json={"user_id": uid}, headers={"X-Admin-Secret": SECRET},
    )


async def _job(db_client, job_id):
    return await db_client.get_one("instance_jobs", {"job_id": job_id})


@pytest.mark.asyncio
async def test_suspend_then_reinstate_resumes_the_paused_jobs_forward(db_client, monkeypatch):
    from datetime import datetime, timezone

    await _seed_user(db_client)
    await _seed_user(db_client, user_id="u_owner_ok")
    await _seed_job(db_client, "job_own", UID, status="active")
    await _seed_job(db_client, "job_delegated", "u_owner_ok", status="pending", related_entity_id=UID)
    await _seed_job(db_client, "job_user_paused", UID, status="paused", paused_reason="user")
    app = _make_app(db_client, monkeypatch)

    suspended = await _post(
        app, "/api/admin/suspend", json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )
    assert suspended.json()["jobs_paused"] == 2

    resp = await _reinstate(app)

    assert resp.status_code == 200
    assert resp.json() == {"reinstated": True, "jobs_resumed": 2, "jobs_resume_error": None}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for job_id in ("job_own", "job_delegated"):
        row = await _job(db_client, job_id)
        assert row["status"] == "active"
        assert row["paused_reason"] is None
        # Scheduled forward from now: the fires missed while suspended are
        # not replayed (the seeded 2020 next_run_time is gone).
        nrt = row["next_run_time"]
        nrt = datetime.fromisoformat(str(nrt)) if not isinstance(nrt, datetime) else nrt
        assert nrt.replace(tzinfo=None) > now
    # The user's own pause survives both halves.
    row = await _job(db_client, "job_user_paused")
    assert (row["status"], row["paused_reason"]) == ("paused", "user")


@pytest.mark.asyncio
async def test_suspend_then_reinstate_keeps_dependency_blocked_jobs_blocked(db_client, monkeypatch):
    """Review I1: a job waiting on a dependency must come out of a
    suspend/reinstate cycle still waiting — never ACTIVE with next_run=now,
    which would run it before its upstream output exists."""
    await _seed_user(db_client)
    await _seed_job(db_client, "job_live", UID, status="active")
    await _seed_job(db_client, "job_blocked", UID, status="blocked")
    await _seed_job(db_client, "job_blocked_failed", UID, status="blocked_failed")
    await _seed_job(db_client, "job_running", UID, status="running")
    app = _make_app(db_client, monkeypatch)

    suspended = await _post(
        app, "/api/admin/suspend", json={"user_id": UID}, headers={"X-Admin-Secret": SECRET},
    )
    assert suspended.json()["jobs_paused"] == 1
    for job_id, status in (
        ("job_blocked", "blocked"), ("job_blocked_failed", "blocked_failed"),
        ("job_running", "running"),
    ):
        row = await _job(db_client, job_id)
        assert (row["status"], row["paused_reason"]) == (status, None)

    resp = await _reinstate(app)

    assert resp.json()["jobs_resumed"] == 1
    assert (await _job(db_client, "job_live"))["status"] == "active"
    assert (await _job(db_client, "job_blocked"))["status"] == "blocked"
    assert (await _job(db_client, "job_blocked_failed"))["status"] == "blocked_failed"
    assert (await _job(db_client, "job_running"))["status"] == "running"


@pytest.mark.asyncio
async def test_reinstate_leaves_non_suspension_pauses_alone(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    await _seed_user(db_client, user_id="u_principal_ok")
    await _seed_job(db_client, "job_user", UID, status="paused", paused_reason="user")
    await _seed_job(db_client, "job_null", UID, status="paused")
    await _seed_job(db_client, "job_quota", UID, status="paused_no_quota")
    await _seed_job(db_client, "job_runs_as_other", UID, status="paused", paused_reason="banned",
                    related_entity_id="u_principal_ok")
    await _seed_job(db_client, "job_gate_banned", UID, status="paused", paused_reason="banned")
    app = _make_app(db_client, monkeypatch)

    resp = await _reinstate(app)

    assert resp.json()["jobs_resumed"] == 1
    assert (await _job(db_client, "job_gate_banned"))["status"] == "active"
    assert (await _job(db_client, "job_user"))["status"] == "paused"
    assert (await _job(db_client, "job_null"))["status"] == "paused"
    assert (await _job(db_client, "job_quota"))["status"] == "paused_no_quota"
    assert (await _job(db_client, "job_runs_as_other"))["status"] == "paused"


@pytest.mark.asyncio
async def test_reinstate_completes_a_job_already_past_its_end_at(db_client, monkeypatch):
    await _seed_user(db_client, status="banned")
    await _seed_job(
        db_client, "job_expired", UID, status="paused", paused_reason="banned",
        trigger_config='{"cron":"0 8 * * *","timezone":"Asia/Shanghai","end_at":"2021-01-01T00:00:00"}',
    )
    app = _make_app(db_client, monkeypatch)

    resp = await _reinstate(app)

    assert resp.json()["jobs_resumed"] == 0
    row = await _job(db_client, "job_expired")
    assert row["status"] == "completed"
    assert row["next_run_time"] is None


@pytest.mark.asyncio
async def test_reinstate_survives_a_job_resume_failure_and_still_audits(db_client, monkeypatch):
    from narranexus.platform.repository.job_repository import JobRepository

    await _seed_user(db_client, status="banned")
    await _seed_job(db_client, "job_1", UID, status="paused", paused_reason="banned")
    app = _make_app(db_client, monkeypatch)

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("instance_jobs unavailable")

    monkeypatch.setattr(JobRepository, "get_jobs_paused_for_execution_principal", _boom)

    resp = await _reinstate(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["reinstated"] is True and body["jobs_resumed"] == 0
    assert "instance_jobs unavailable" in body["jobs_resume_error"]
    assert (await db_client.get_one("users", {"user_id": UID}))["status"] == "active"
    audit = await db_client.get("ban_audit", {"user_id": UID})
    assert [a["action"] for a in audit] == ["reinstate"]
    assert (await _job(db_client, "job_1"))["status"] == "paused"


@pytest.mark.asyncio
async def test_reinstate_reports_when_builtin_job_is_not_loaded(db_client, monkeypatch):
    import backend.routes.admin.suspend as mod

    await _seed_user(db_client, status="banned")
    await _seed_job(db_client, "job_1", UID, status="paused", paused_reason="banned")
    app = _make_app(db_client, monkeypatch)
    monkeypatch.setattr(mod, "try_job_resume_for_principal", lambda: None)

    resp = await _reinstate(app)

    body = resp.json()
    assert body["reinstated"] is True and body["jobs_resumed"] == 0
    assert "builtin.job is not loaded" in body["jobs_resume_error"]
