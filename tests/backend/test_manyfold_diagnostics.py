"""
@file_name: test_manyfold_diagnostics.py
@author:
@date: 2026-08-10
@description: Per-agent pull channel (observability plan A) — audit-trail
query, event summaries/full fetch with ownership + redaction, and the
service-log tail behind the gateway token.
"""
from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

import backend.routes.manyfold.diagnostics as diag_mod


@pytest.fixture
def diag_app(db_client, monkeypatch):
    async def fake_db():
        return db_client

    monkeypatch.setattr(diag_mod, "get_db_client", fake_db)

    app = FastAPI()

    @app.middleware("http")
    async def _authed(request: Request, call_next):
        request.state.manyfold_authed = True
        return await call_next(request)

    app.include_router(diag_mod.router)
    return app


async def _get(app, path, **params):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # params={} would REPLACE a query string already in `path`.
        return await c.get(path, params=params or None)


@pytest.fixture
def unauthed_app(db_client, monkeypatch):
    async def fake_db():
        return db_client

    monkeypatch.setattr(diag_mod, "get_db_client", fake_db)
    app = FastAPI()
    app.include_router(diag_mod.router)
    return app


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/manyfold/agents/a1/diagnostics/ingress",
        "/manyfold/agents/a1/diagnostics/events",
        "/manyfold/agents/a1/diagnostics/events/evt_x",
        "/manyfold/diagnostics/logs/services",
        "/manyfold/diagnostics/logs/tail?service=backend",
    ],
)
async def test_all_endpoints_require_gateway_auth(unauthed_app, path):
    resp = await _get(unauthed_app, path)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Ingress audit query
# ---------------------------------------------------------------------------


async def _seed_audit(db_client):
    rows = [
        ("2026-08-10 10:00:00", "managed_ingress_processed", "wechat", "a1"),
        ("2026-08-10 11:00:00", "managed_ingress_denied", "narramessenger", "a1"),
        ("2026-08-10 12:00:00", "manyfold_files_write", "manyfold", "a1"),
        ("2026-08-10 12:30:00", "managed_ingress_processed", "wechat", "OTHER"),
    ]
    for event_time, event_type, channel, agent in rows:
        await db_client.insert(
            "channel_trigger_audit",
            {
                "channel": channel,
                "event_time": event_time,
                "event_type": event_type,
                "message_id": "m1",
                "agent_id": agent,
                "app_id": "",
                "chat_id": "room1",
                "sender_id": "u9",
                "details": json.dumps({"ok": True, "reply_token": "SECRET"}),
            },
        )


async def test_ingress_is_agent_scoped_and_newest_first(diag_app, db_client):
    await _seed_audit(db_client)
    resp = await _get(diag_app, "/manyfold/agents/a1/diagnostics/ingress")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 3  # OTHER agent's row excluded
    assert [r["event_type"] for r in data] == [
        "manyfold_files_write",
        "managed_ingress_denied",
        "managed_ingress_processed",
    ]


async def test_ingress_filters_and_redaction(diag_app, db_client):
    await _seed_audit(db_client)
    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        event_type="managed_ingress_denied",
    )
    data = resp.json()["data"]
    assert [r["event_type"] for r in data] == ["managed_ingress_denied"]
    # Credential keys in details never leave the sandbox.
    assert data[0]["details"]["reply_token"] == "[redacted]"

    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        since="2026-08-10 11:30:00",
    )
    assert [r["event_type"] for r in resp.json()["data"]] == [
        "manyfold_files_write"
    ]


async def test_since_accepts_iso_t_form_against_real_write_path(
    diag_app, db_client
):
    """Regression: audit rows are written via the repository
    (isoformat(sep=" ") — SPACE form). A caller's ISO `since` with the
    'T' separator must still match; lexicographic comparison without
    normalization silently returned empty."""
    from narranexus.platform.repository.channel_trigger_audit_repository import (
        ChannelTriggerAuditRepository,
    )

    repo = ChannelTriggerAuditRepository("wechat", db_client)
    await repo.append(
        "managed_ingress_processed", agent_id="a1", details={"replied": True}
    )
    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        since="2000-01-01T00:00:00",  # T form, far past — must NOT filter out
    )
    assert len(resp.json()["data"]) == 1
    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        since="2999-01-01T00:00:00",  # T form, far future — filters all
    )
    assert resp.json()["data"] == []


async def test_since_z_form_boundary_second(diag_app, db_client):
    """`…T09:00:00Z` must include rows AT 09:00:00.x — the old
    lexicographic fold left the 'Z' in place and 'Z' > '.' pushed
    boundary-second rows below the floor."""
    await db_client.insert(
        "channel_trigger_audit",
        {
            "channel": "wechat",
            "event_time": "2026-08-10 09:00:00.500000+00:00",
            "event_type": "managed_ingress_processed",
            "message_id": "m1",
            "agent_id": "a1",
            "app_id": "",
            "chat_id": "c",
            "sender_id": "s",
            "details": "{}",
        },
    )
    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        since="2026-08-10T09:00:00Z",
    )
    assert len(resp.json()["data"]) == 1


async def test_bad_since_is_400_not_silent_empty(diag_app, db_client):
    resp = await _get(
        diag_app,
        "/manyfold/agents/a1/diagnostics/ingress",
        since="not-a-date",
    )
    assert resp.status_code == 400


async def test_summary_error_message_is_redacted(diag_app, db_client):
    await db_client.insert(
        "events",
        {
            "event_id": "evt_err",
            "trigger": "user_message",
            "trigger_source": "wechat",
            "agent_id": "a1",
            "state": "failed",
            "started_at": "2026-08-10 12:00:00",
            "error_message": 'upstream 401: {"api_key": "sk-SECRET"} Bearer abcdefgh12345',
        },
    )
    resp = await _get(diag_app, "/manyfold/agents/a1/diagnostics/events")
    row = resp.json()["data"][0]
    assert "sk-SECRET" not in row["error_message"]
    assert "abcdefgh12345" not in row["error_message"]


async def test_ingress_limit_clamped(diag_app, db_client):
    await _seed_audit(db_client)
    resp = await _get(
        diag_app, "/manyfold/agents/a1/diagnostics/ingress", limit=100000
    )
    assert resp.status_code == 200  # clamp, not reject
    resp2 = await _get(
        diag_app, "/manyfold/agents/a1/diagnostics/ingress", limit=1
    )
    assert len(resp2.json()["data"]) == 1


# ---------------------------------------------------------------------------
# Events summary + full fetch
# ---------------------------------------------------------------------------


async def _seed_events(db_client):
    await db_client.insert(
        "events",
        {
            "event_id": "evt_1",
            "trigger": "user_message",
            "trigger_source": "wechat",
            "env_context": json.dumps(
                {"input": "hi", "context_token": "TOPSECRET"}
            ),
            "module_instances": "{}",
            "event_log": "L" * 10,
            "final_output": "hello back",
            "narrative_id": "nar_1",
            "agent_id": "a1",
            "user_id": "u1",
            "state": "completed",
            "started_at": "2026-08-10 10:00:00",
            "finished_at": "2026-08-10 10:00:05",
            "tool_call_count": 2,
        },
    )
    await db_client.insert(
        "events",
        {
            "event_id": "evt_foreign",
            "trigger": "user_message",
            "trigger_source": "chat",
            "agent_id": "OTHER",
            "state": "completed",
            "started_at": "2026-08-10 11:00:00",
        },
    )


async def test_events_summary_has_no_content_fields(diag_app, db_client):
    await _seed_events(db_client)
    resp = await _get(diag_app, "/manyfold/agents/a1/diagnostics/events")
    data = resp.json()["data"]
    assert len(data) == 1
    row = data[0]
    assert row["event_id"] == "evt_1"
    assert row["state"] == "completed"
    assert row["tool_call_count"] == 2
    # Content never leaves the DB for the list — SQL projection excludes
    # the MEDIUMTEXT columns entirely (a long-running agent's event_log
    # set must not be materialized to serve a summary).
    assert "env_context" not in row and "final_output" not in row
    assert "sizes" not in row


async def test_event_full_fetch_redacts_and_scopes(diag_app, db_client):
    await _seed_events(db_client)
    resp = await _get(diag_app, "/manyfold/agents/a1/diagnostics/events/evt_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_output"] == "hello back"
    assert "TOPSECRET" not in body["env_context"]
    assert "[redacted]" in body["env_context"]

    # Another agent's event 404s identically to a missing one.
    resp = await _get(
        diag_app, "/manyfold/agents/a1/diagnostics/events/evt_foreign"
    )
    assert resp.status_code == 404
    resp = await _get(
        diag_app, "/manyfold/agents/a1/diagnostics/events/evt_nope"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Logs tail
# ---------------------------------------------------------------------------


def _write_log(tmp_path, service="backend"):
    d = tmp_path / service
    d.mkdir(parents=True)
    today = date.today().strftime("%Y%m%d")
    lines = [
        "2026-08-10 10:00:00.000 | INFO     | run1 evt1 | m:f:1 - hello world",
        "2026-08-10 10:00:01.000 | WARNING  | run1 evt1 | m:f:2 - Bearer abcdef123456789 leaked",
        "2026-08-10 10:00:02.000 | INFO     | run2 evt2 | m:f:3 - other line",
    ]
    (d / f"{service}_{today}.log").write_text("\n".join(lines) + "\n")


async def test_logs_tail_with_level_grep_and_redaction(
    diag_app, tmp_path, monkeypatch
):
    monkeypatch.setenv("NEXUS_LOG_DIR", str(tmp_path))
    _write_log(tmp_path)

    resp = await _get(
        diag_app, "/manyfold/diagnostics/logs/tail", service="backend"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["lines"]) == 3
    # Bearer tokens never leave the sandbox.
    assert "abcdef123456789" not in "\n".join(body["lines"])

    resp = await _get(
        diag_app,
        "/manyfold/diagnostics/logs/tail",
        service="backend",
        level="warning",
    )
    assert len(resp.json()["lines"]) == 1

    resp = await _get(
        diag_app,
        "/manyfold/diagnostics/logs/tail",
        service="backend",
        grep="other line",
    )
    assert len(resp.json()["lines"]) == 1


async def test_logs_tail_rejects_bad_service(diag_app, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LOG_DIR", str(tmp_path))
    resp = await _get(
        diag_app, "/manyfold/diagnostics/logs/tail", service="../etc"
    )
    assert resp.status_code == 400
    resp = await _get(
        diag_app, "/manyfold/diagnostics/logs/tail", service="ghost"
    )
    assert resp.status_code == 404


async def test_logs_services_lists_dirs(diag_app, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_LOG_DIR", str(tmp_path))
    _write_log(tmp_path, "backend")
    _write_log(tmp_path, "mcp")
    resp = await _get(diag_app, "/manyfold/diagnostics/logs/services")
    body = resp.json()
    assert set(body["services"]) == {"backend", "mcp"}
    assert any(name.startswith("backend_") for name in body["services"]["backend"])
