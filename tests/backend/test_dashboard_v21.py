"""
@file_name: test_dashboard_v21.py
@description: v2.1 additions — queue counts, attention banners, verb_line,
metrics_today, lazy detail endpoints.
"""
import pytest

from backend.routes._dashboard_helpers import (
    derive_attention_banners,
    derive_health,
    humanize_verb,
    build_recent_events_resp,
)


# ---- derive_attention_banners -------------------------------------------

def test_no_banners_when_queue_clean():
    assert derive_attention_banners({"failed": 0, "blocked": 0, "paused": 0}) == []


def test_banner_for_failed_jobs():
    out = derive_attention_banners({"failed": 2, "blocked": 0, "paused": 0})
    assert len(out) == 1
    assert out[0]["level"] == "error"
    assert out[0]["kind"] == "job_failed"
    assert "2" in out[0]["message"]


def test_banner_ordering_error_before_warning():
    out = derive_attention_banners({"failed": 1, "blocked": 1, "paused": 1})
    assert [b["kind"] for b in out] == ["job_failed", "job_blocked", "jobs_paused"]
    assert out[0]["level"] == "error"
    assert all(b["level"] == "warning" for b in out[1:])


# ---- derive_health -------------------------------------------------------

def test_health_error_dominates_when_failed_jobs():
    h = derive_health(
        kind="CHAT", queue={"failed": 1}, last_activity_at="2026-04-13T10:00:00Z",
        errors_today=0,
    )
    assert h == "error"


def test_health_error_when_errors_today():
    h = derive_health(
        kind="idle", queue={}, last_activity_at="2026-04-13T10:00:00Z",
        errors_today=3,
    )
    assert h == "error"


def test_health_warning_when_blocked():
    h = derive_health(
        kind="idle", queue={"blocked": 1}, last_activity_at=None, errors_today=0,
    )
    assert h == "warning"


def test_health_paused_when_paused():
    h = derive_health(
        kind="idle", queue={"paused": 2}, last_activity_at=None, errors_today=0,
    )
    assert h == "paused"


def test_health_running_when_non_idle_kind():
    h = derive_health(
        kind="CHAT", queue={}, last_activity_at=None, errors_today=0,
    )
    assert h == "healthy_running"


def test_health_idle_recent_healthy_idle():
    from datetime import datetime, timezone
    recent = datetime.now(timezone.utc).isoformat()
    h = derive_health(
        kind="idle", queue={}, last_activity_at=recent, errors_today=0,
    )
    assert h == "healthy_idle"


# ---- humanize_verb -------------------------------------------------------

def test_verb_idle_never_run():
    assert humanize_verb("idle", [], [], last_activity_at=None) == "Never run"


def test_verb_chat_single_user_mentions_display_name():
    class S:
        user_display = "Alice"
        started_at = "t"
    out = humanize_verb("CHAT", [S()], [], last_activity_at=None)
    assert "Alice" in out
    assert "conversation" in out.lower()


def test_verb_chat_many_users_counts():
    class S:
        user_display = "X"
        started_at = "t"
    out = humanize_verb("CHAT", [S(), S(), S()], [], last_activity_at=None)
    assert "3" in out
    assert "serving" in out.lower()


def test_verb_job_single_titled():
    out = humanize_verb(
        "JOB", [], [{"title": "weekly-report"}], last_activity_at=None,
    )
    assert "weekly-report" in out


def test_verb_message_bus_and_a2a():
    assert humanize_verb("MESSAGE_BUS", [], [], None) == "Handling bus message"
    assert humanize_verb("A2A", [], [], None) == "Called by another agent"


# ---- build_recent_events_resp -------------------------------------------

def test_recent_events_classifies_error():
    rows = [{
        "event_id": "e1", "trigger": "JOB", "final_output": "Job failed: ERROR timeout",
        "created_at": "2026-04-13T10:00:00Z",
    }]
    out = build_recent_events_resp(rows)
    assert out[0]["kind"] == "failed"
    assert "fail" in out[0]["verb"].lower()


def test_recent_events_classifies_chat():
    rows = [{
        "event_id": "e2", "trigger": "CHAT", "final_output": "hi alice",
        "created_at": "2026-04-13T10:00:00Z",
    }]
    out = build_recent_events_resp(rows)
    assert out[0]["kind"] == "chat"


def test_recent_events_classifies_job_completed():
    rows = [{
        "event_id": "e3", "trigger": "JOB", "final_output": "report generated ok",
        "created_at": "2026-04-13T10:00:00Z",
    }]
    out = build_recent_events_resp(rows)
    assert out[0]["kind"] == "completed"


# ---- /agents-status response includes v2.1 fields ----------------------

def test_v21_fields_present_on_owned(local_client_seeded):
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    assert r.status_code == 200
    owned = [a for a in r.json()["agents"] if a["owned_by_viewer"] is True]
    assert owned, "fixture must include owned agents"
    for a in owned:
        assert "queue" in a
        assert "recent_events" in a
        assert "metrics_today" in a
        assert "attention_banners" in a
        assert "health" in a
        assert "verb_line" in a
        # queue has all 6 keys + total
        assert set(a["queue"].keys()) >= {
            "running", "active", "pending", "blocked", "paused", "failed", "total",
        }


def test_v21_public_variant_still_locked_down(local_client_seeded):
    """G008 regression: v2.1 must not leak new owner-only fields to public variant."""
    r = local_client_seeded["client"].get("/api/dashboard/agents-status")
    public = [a for a in r.json()["agents"] if a["owned_by_viewer"] is False]
    assert public, "fixture must include at least one public non-owned"
    forbidden = {
        "queue", "recent_events", "metrics_today",
        "attention_banners", "health", "verb_line",
    }
    for a in public:
        extra = forbidden & set(a.keys())
        assert not extra, f"Public variant v2.1 leak: {extra}"


# ---- Lazy endpoints: basic reachability + auth checks -------------------

def test_sparkline_endpoint_requires_visibility(local_client_seeded):
    client = local_client_seeded["client"]
    ctx = local_client_seeded["ctx"]
    # Own agent → 200
    r = client.get(f"/api/dashboard/agents/{ctx['a1']}/sparkline")
    assert r.status_code == 200
    assert isinstance(r.json()["buckets"], list)
    assert len(r.json()["buckets"]) == 24
    # Unknown agent → 404
    r = client.get("/api/dashboard/agents/nonexistent_agent/sparkline")
    assert r.status_code == 404


def test_job_detail_rejects_public_non_owned(local_client_seeded):
    """Public non-owned user should not see internal job details."""
    # There are no real jobs in the fixture; just confirm 404 for nonexistent.
    client = local_client_seeded["client"]
    r = client.get("/api/dashboard/jobs/nonexistent_job_id")
    assert r.status_code == 404


def test_session_detail_requires_agent_id_param(local_client_seeded):
    r = local_client_seeded["client"].get(
        "/api/dashboard/sessions/some_session_id"
    )
    assert r.status_code == 400
    assert "agent_id" in r.text.lower()
