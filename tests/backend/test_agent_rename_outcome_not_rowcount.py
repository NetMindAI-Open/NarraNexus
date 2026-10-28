"""
@file_name: test_agent_rename_outcome_not_rowcount.py
@author: NarraNexus
@date: 2026-08-17
@description: PUT /api/auth/agents/{id} must report the STORED OUTCOME, never
the driver's rowcount.

`AgentRepository.update_agent` returns `cursor.rowcount`, which counts MATCHED
rows on SQLite but CHANGED rows on MySQL. The route inferred failure from
`affected_rows > 0`, so on cloud (MySQL) a rename whose value was already
stored answered `success=False, error="No changes made"` for a row that holds
exactly what the caller asked for. The user reads that as "rename failed",
retries, and gets the same answer forever — while the DB has the new name all
along. `_awareness_writes.py` defused this same trap for the agent-facing tool
in 2026-08-05; this is the user-facing HTTP twin.

The tests force the MySQL reading of rowcount (return 0) because the SQLite
fixture returns 1 for a no-op write and would pass vacuously on the very
dialect the bug cannot occur on.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from narranexus.platform.repository import AgentRepository
from narranexus.platform.repository.user_repository import UserRepository

AGENT_ID = "agent_rename_test"
OWNER = "alice"


async def _async_return(value):
    return value


@pytest.fixture
def client(db_client, monkeypatch):
    import backend.routes.auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_db_client", lambda: _async_return(db_client))

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(auth_mod.router, prefix="/api/auth")
    return TestClient(app)


@pytest.fixture
def seeded(db_client):
    """One owner with one agent already named 小绿 (the post-rename state)."""

    async def _seed():
        users = UserRepository(db_client)
        await users.add_user(user_id=OWNER, user_type="individual")
        agents = AgentRepository(db_client)
        await agents.add_agent(
            agent_id=AGENT_ID,
            agent_name="小绿",
            agent_description="精通各地美食推荐",
            created_by=OWNER,
        )

    asyncio.get_event_loop().run_until_complete(_seed())


def _stored_name(db_client) -> str:
    async def _read():
        agent = await AgentRepository(db_client).get_agent(AGENT_ID)
        return agent.agent_name

    return asyncio.get_event_loop().run_until_complete(_read())


def test_resaving_the_stored_name_is_success_not_no_changes_made(
    client, db_client, seeded, monkeypatch
):
    """The desired state already holds → that is success, not a failure.

    On MySQL an identical re-save changes zero rows. Reporting that as an
    error is what made the tester retry a rename that had already landed.
    """
    calls: list[dict] = []

    async def _mysql_noop(self, agent_id, updates):  # noqa: ANN001
        calls.append(updates)
        return 0  # MySQL: CHANGED rows

    monkeypatch.setattr(AgentRepository, "update_agent", _mysql_noop)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True, body.get("error")
    assert body["agent"]["name"] == "小绿"
    # A value that already matches is not a write at all — the equality
    # short-circuit is what makes the answer dialect-independent.
    assert calls == [], f"no-op re-save still issued a write: {calls}"
    assert _stored_name(db_client) == "小绿"


def test_a_real_change_reported_as_zero_rows_still_succeeds(
    client, db_client, seeded, monkeypatch
):
    """rowcount is advisory; the re-read is the verdict.

    A genuine change whose driver reports 0 (collation-equal values, a pooled
    connection without CLIENT_FOUND_ROWS) must be judged by what the row now
    holds, not by the counter.
    """
    real_update = AgentRepository.update_agent

    async def _writes_but_reports_zero(self, agent_id, updates):  # noqa: ANN001
        await real_update(self, agent_id, updates)
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _writes_but_reports_zero)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小蓝"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True, body.get("error")
    assert body["agent"]["name"] == "小蓝"
    assert _stored_name(db_client) == "小蓝"


def test_a_write_that_truly_did_not_land_is_still_a_failure(
    client, db_client, seeded, monkeypatch
):
    """The guard must not become "always success".

    If the row does not hold what was asked after the write, the caller has to
    hear about it — that is the one case `success=False` exists for.
    """

    async def _swallows_the_write(self, agent_id, updates):  # noqa: ANN001
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _swallows_the_write)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小蓝"},
        headers={"X-User-Id": OWNER},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False
    assert body["error"]
    assert _stored_name(db_client) == "小绿"


def test_visibility_toggle_really_writes_under_a_zero_rowcount_driver(
    client, db_client, seeded, monkeypatch
):
    """The `is_public` branch, asserted on the WRITE — not on `success`.

    A predicate that wrongly judged the toggle "already equal" would skip the
    write and then certify the unchanged row, so `success=True` proves
    nothing here. This is the quietest failure the design allows: no error,
    no rowcount anomaly, the switch just does not move.
    """
    calls: list[dict] = []
    real_update = AgentRepository.update_agent

    async def _writes_but_reports_zero(self, agent_id, updates):  # noqa: ANN001
        calls.append(dict(updates))
        await real_update(self, agent_id, updates)
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _writes_but_reports_zero)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"is_public": True},
        headers={"X-User-Id": OWNER},
    )

    assert res.json()["success"] is True, res.json().get("error")
    assert calls == [{"is_public": 1}], f"toggle issued no write: {calls}"

    async def _read_flag():
        agent = await AgentRepository(db_client).get_agent(AGENT_ID)
        return bool(agent.is_public)

    assert asyncio.get_event_loop().run_until_complete(_read_flag()) is True


def test_re_toggling_to_the_current_visibility_writes_nothing_and_succeeds(
    client, db_client, seeded, monkeypatch
):
    calls: list[dict] = []

    async def _spy(self, agent_id, updates):  # noqa: ANN001
        calls.append(dict(updates))
        return 0

    monkeypatch.setattr(AgentRepository, "update_agent", _spy)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"is_public": False},
        headers={"X-User-Id": OWNER},
    )

    assert res.json()["success"] is True
    assert calls == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_name_is_refused_the_way_the_agent_facing_tool_refuses_it(
    client, db_client, seeded, blank
):
    """An unnamed agent renders its raw agent_id everywhere.

    `update_agent_profile_from_args` has always rejected this; the HTTP route
    accepted it, so the same input was refused on one path and stored on the
    other. Whitespace-only is the same request.
    """
    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": blank},
        headers={"X-User-Id": OWNER},
    )

    body = res.json()
    assert body["success"] is False
    assert "empty" in body["error"].lower()
    assert _stored_name(db_client) == "小绿"


def test_surrounding_whitespace_is_stripped_before_it_is_stored(
    client, db_client, seeded
):
    """Compare-then-verify only holds if what we compare is what we store."""
    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "  小蓝  "},
        headers={"X-User-Id": OWNER},
    )

    assert res.json()["success"] is True
    assert res.json()["agent"]["name"] == "小蓝"
    assert _stored_name(db_client) == "小蓝"


def test_rename_persists_and_is_visible_to_the_list_endpoint(
    client, db_client, seeded
):
    """The ticket's acceptance path: rename, then re-read the list.

    Runs against the unpatched repository so it also covers the ordinary
    SQLite path end to end.
    """
    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿2", "agent_description": "精通各地美食推荐"},
        headers={"X-User-Id": OWNER},
    )
    assert res.json()["success"] is True

    listed = client.get("/api/auth/agents", headers={"X-User-Id": OWNER}).json()
    assert listed["success"] is True
    names = {a["agent_id"]: a["name"] for a in listed["agents"]}
    assert names[AGENT_ID] == "小绿2"


def test_a_no_op_re_save_still_refreshes_the_peer_directory(
    client, db_client, seeded, monkeypatch
):
    """Re-saving the same values is how a user retries, so it must not be the
    one path that skips the discovery refresh.

    The refresh exists because "the agent's next turn will rewrite it" was not
    good enough — an agent edited and left idle stayed undiscoverable (P1
    section 02) — and an idle agent is exactly the case that cannot self-heal.
    `sync_agent_discovery` swallows its own failures, so a peer directory stuck
    on the old name has no other way back.
    """
    calls: list[str] = []

    async def _spy(_db, agent_id):  # noqa: ANN001
        calls.append(agent_id)
        return True

    # Patch the DEFINING module, not the route: the route no longer holds the
    # symbol — it calls the shared rename transaction, which imports
    # sync_agent_discovery inside the function. Patching a name the route does
    # not import would silently spy on nothing and pass on any behaviour.
    import narranexus.platform.message_bus.agent_discovery_sync as discovery_mod

    monkeypatch.setattr(discovery_mod, "sync_agent_discovery", _spy)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},  # already the stored value
        headers={"X-User-Id": OWNER},
    )
    assert res.json()["success"] is True
    assert calls == [AGENT_ID], (
        "a no-op re-save skipped the peer-directory refresh, so a stale "
        "directory has no user-triggerable repair path"
    )


def test_a_field_that_needed_no_write_is_still_verified_against_the_row(
    client, db_client, seeded, monkeypatch
):
    """The route owes the caller "is the row what you asked for", not merely
    "did my write land".

    A field that compares equal at read time issues no write, so it is not on
    the shared transaction's verify list — but a concurrent writer (a second
    tab, or the agent's own ``update_agent_profile``) can still move it inside
    the read-write-reread window. Benign last-write-wins; the caller is told no
    regardless, because the row genuinely is not in the requested state.

    This was unpinned when the route delegated its write to the shared
    transaction, and the delegation silently narrowed it to the written fields
    only.
    """
    import backend.routes.auth as auth_mod
    from narranexus.platform.agent_profile import AgentProfileWrite

    async def _concurrent_writer(db, agent_id, **_kwargs):  # noqa: ANN001
        """No-op write, then someone else renames the row underneath us."""
        await db.update("agents", {"agent_id": agent_id}, {"agent_name": "小蓝"})
        return AgentProfileWrite(status="unchanged")

    monkeypatch.setattr(auth_mod, "apply_agent_profile_change", _concurrent_writer)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小绿"},  # already the stored value → no write
        headers={"X-User-Id": OWNER},
    )

    body = res.json()
    assert body["success"] is False
    assert "agent_name" in body["error"]


def test_a_transaction_level_failure_names_the_fields_that_did_not_land(
    client, db_client, seeded, monkeypatch
):
    """The `not_applied` branch names fields, and it names them from the right
    list.

    ``AgentProfileWrite`` carries two lists — what was written and what failed
    to land — and they were briefly the same attribute. Nothing covered this
    branch, so reading the wrong one would have produced the bare string
    "The update did not persist: " with no fields, silently.
    """
    import backend.routes.auth as auth_mod
    from narranexus.platform.agent_profile import AgentProfileWrite

    async def _did_not_land(_db, _agent_id, **_kwargs):  # noqa: ANN001
        return AgentProfileWrite(
            status="error",
            error_kind="not_applied",
            error="Error: the update did not apply; nothing was changed",
            unapplied_fields=("agent_name",),
        )

    monkeypatch.setattr(auth_mod, "apply_agent_profile_change", _did_not_land)

    res = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "小蓝"},
        headers={"X-User-Id": OWNER},
    )

    body = res.json()
    assert body["success"] is False
    assert body["error"] == "The update did not persist: agent_name"


def test_a_refusal_is_worded_for_the_ui_not_for_a_model(
    client, db_client, seeded, monkeypatch
):
    """The shared transaction's `error` is composed for a MODEL to read.

    "Error: new_name is too long (max 255 characters)" in a dialog reads as a
    leaked internal string, and the two audiences drift the moment either is
    reworded. The route maps on error_kind and writes its own sentence.
    """
    import backend.routes.auth as auth_mod
    from narranexus.platform.agent_profile import AgentProfileWrite

    async def _too_long(_db, _agent_id, **_kwargs):  # noqa: ANN001
        return AgentProfileWrite(
            status="error",
            error_kind="too_long",
            error="Error: new_name is too long (max 255 characters)",
        )

    monkeypatch.setattr(auth_mod, "apply_agent_profile_change", _too_long)

    body = client.put(
        f"/api/auth/agents/{AGENT_ID}",
        json={"agent_name": "x"},
        headers={"X-User-Id": OWNER},
    ).json()

    assert body["success"] is False
    assert not body["error"].startswith("Error:"), body["error"]
    assert "255" in body["error"]
