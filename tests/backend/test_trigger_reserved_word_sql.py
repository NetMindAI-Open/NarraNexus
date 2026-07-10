"""
@file_name: test_trigger_reserved_word_sql.py
@author: NarraNexus
@date: 2026-07-07
@description: Regression for the MySQL reserved-word `trigger` 1064 in the two
raw-SQL paths that reference the events.trigger column unquoted.

Root cause: `trigger` is a MySQL reserved word. Two hand-written queries used
it bare:
  1. GET /api/auth/agents  — last_assistant sidebar preview
     (backend.routes.auth.get_agents). On prod (MySQL) every call raised
     (1064, "... near 'trigger IS NULL OR trigger != 'message_bus'"); 2585
     WARNINGs in 2 days, sidebar previews silently missing.
  2. Dashboard "recent activity" feed
     (backend.routes._dashboard_helpers.fetch_recent_events). Same 1064, but
     swallowed by a bare `except` -> the feed silently returned empty.

Why local dev never caught it: the test DB is in-memory SQLite, and SQLite
tolerates an unquoted `trigger`. So a plain SQLite test passes on the broken
code. `MySQLReservedWordDB` below closes that gap: it rejects an unquoted
`trigger` exactly as MySQL does, so the bug reproduces in-process and the
backtick fix is verified end-to-end (not just asserted on the SQL string).
"""
from __future__ import annotations

import asyncio
import re

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import backend.routes.auth as auth_mod
import backend.routes._dashboard_helpers as dash_mod
from xyz_agent_context.repository.user_repository import UserRepository


# An unquoted `trigger` used as an identifier: word-boundary `trigger` that is
# NOT backtick-quoted and NOT part of trigger_source / trigger_type / etc.
# `ORDER BY` / `GROUP BY` never contain the substring, so they are not flagged.
_BARE_TRIGGER = re.compile(r"(?<![`\w])trigger(?![`\w])", re.IGNORECASE)


class MySQLReservedWordDB:
    """Wrap a SQLite AsyncDatabaseClient so it rejects an unquoted MySQL
    reserved word `trigger`, the way prod MySQL does. Everything else is
    delegated untouched, so seeding via insert()/get_one() still works on
    SQLite. Only execute() is guarded — that is where the two broken raw
    queries run."""

    def __init__(self, inner):
        self._inner = inner

    async def execute(self, query, params=None, fetch=True):
        if _BARE_TRIGGER.search(query):
            # Mirror pymysql's ProgrammingError payload closely enough that the
            # production `except Exception` paths behave identically.
            raise Exception(
                "(1064, \"You have an error in your SQL syntax; check the manual "
                "that corresponds to your MySQL server version for the right "
                "syntax to use near 'trigger'\")"
            )
        return await self._inner.execute(query, params, fetch)

    def __getattr__(self, name):
        # insert / get_one / update / etc. -> straight through to SQLite.
        return getattr(self._inner, name)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _seed_event(db, *, agent_id, trigger, final_output, created_at):
    """Insert a minimal events row (only the NOT-NULL-without-default cols
    plus the fields the queries read)."""
    await db.insert("events", {
        "event_id": f"evt_{trigger}_{created_at[-3:]}",
        "agent_id": agent_id,
        "trigger": trigger,
        "trigger_source": "test",
        "final_output": final_output,
        "state": "completed",
        "created_at": created_at,
    })


# ── Guard sanity: the emulator must actually reject the pre-fix SQL ──────────

def test_emulator_rejects_bare_trigger_but_allows_backticked():
    assert _BARE_TRIGGER.search("WHERE trigger IS NULL OR trigger != 'x'")
    assert _BARE_TRIGGER.search("SELECT event_id, trigger, trigger_source FROM t")
    # Fixed forms and lookalikes must NOT trip it.
    assert not _BARE_TRIGGER.search("WHERE `trigger` IS NULL OR `trigger` != 'x'")
    assert not _BARE_TRIGGER.search("SELECT `trigger`, trigger_source FROM t")
    assert not _BARE_TRIGGER.search("ORDER BY created_at DESC")


# ── Path 1: GET /api/auth/agents sidebar preview ────────────────────────────

@pytest.fixture
def agents_client(db_client, monkeypatch):
    """FastAPI app wiring the auth router with a MySQL-reserved-word-emulating
    DB, so the last_assistant preview query is exercised as it is on prod."""
    wrapped = MySQLReservedWordDB(db_client)

    async def _get_db():
        return wrapped

    monkeypatch.setattr(auth_mod, "get_db_client", _get_db)

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app), db_client


def test_get_agents_sidebar_preview_survives_mysql_and_excludes_message_bus(agents_client):
    client, db_client = agents_client

    _run(UserRepository(db_client).add_user(user_id="alice", user_type="individual"))

    created = client.post(
        "/api/auth/agents",
        headers={"X-User-Id": "alice"},
        json={"agent_name": "A1"},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["agent_id"]

    # Latest reply is a message_bus (group chat) reply -> must be excluded.
    # The 1:1 chat reply is older but is the preview we want to surface.
    _run(_seed_event(db_client, agent_id=agent_id, trigger="chat",
                     final_output="hello from chat", created_at="2026-07-07T10:00:01Z"))
    _run(_seed_event(db_client, agent_id=agent_id, trigger="message_bus",
                     final_output="group bus reply", created_at="2026-07-07T10:00:02Z"))

    resp = client.get("/api/auth/agents", headers={"X-User-Id": "alice"})
    assert resp.status_code == 200
    agent = next(a for a in resp.json()["agents"] if a["agent_id"] == agent_id)

    # Pre-fix: the query 1064s under MySQL emulation, enrichment is swallowed,
    # preview is None. Post-fix (backticked `trigger`): the query runs, the
    # message_bus reply is filtered out, and the 1:1 reply is surfaced.
    assert agent["last_assistant_preview"] == "hello from chat"


# ── Path 2: dashboard recent-activity feed ──────────────────────────────────

def test_fetch_recent_events_survives_mysql(db_client, monkeypatch):
    wrapped = MySQLReservedWordDB(db_client)

    async def _get_db():
        return wrapped

    # fetch_recent_events imports get_db_client from db_factory at call time.
    import xyz_agent_context.utils.db_factory as db_factory
    monkeypatch.setattr(db_factory, "get_db_client", _get_db)

    _run(_seed_event(db_client, agent_id="ag_dash", trigger="job",
                     final_output="did a thing", created_at="2026-07-07T09:00:00Z"))

    out = _run(dash_mod.fetch_recent_events(["ag_dash"], limit_per_agent=3))

    # Pre-fix: 1064 under MySQL emulation -> bare `except` swallows it ->
    # out["ag_dash"] == [] (feed silently empty). Post-fix: the row comes back
    # with its trigger field intact.
    assert len(out["ag_dash"]) == 1
    assert out["ag_dash"][0]["trigger"] == "job"
    assert out["ag_dash"][0]["final_output"] == "did a thing"
