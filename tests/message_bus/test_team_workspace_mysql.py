"""
@file_name: test_team_workspace_mysql.py
@author: NarraNexus
@date: 2026-08-08
@description: Real-MySQL coverage for the team-workspace raw SQL.

Why this file exists
---------------------
The team workspace ships six hand-written statements, all previously exercised
only against `SQLiteBackend(":memory:")`. Two of them are the interesting ones:

  * `_team_artifact_turns` (plugins/builtin.teams/src/narranexus_plugins/teams/routes.py) — `SELECT DISTINCT` over a
    JOIN with `ORDER BY` on an aliased column. That is the exact shape that
    trips ONLY_FULL_GROUP_BY / error 3065 when the ordering column is not in
    the select list.
  * `list_for_agent_context` (repository/artifact_repository.py) — an `OR` of a
    direct predicate and an `IN (SELECT ...)` subquery, plus `LIMIT %s` with a
    bound parameter. A bound LIMIT is dialect-sensitive: some drivers refuse a
    string there, and SQLite silently accepts what MySQL rejects.

The rest (`list_by_team`, `list_pinned`'s new LIMIT, `_team_files`, the
`team_files` dedup probe, and the history bulk delete's expanded IN-list) are
simpler but ship in the same change, so they are covered here rather than left
to be discovered in prod.

Project policy for new raw SQL (see `test_cascade_stop_mysql.py`,
`test_agents_bus_failures_mysql.py`): validate against a real MySQL.
`test_trigger_reserved_word_sql.py` records what skipping it looks like —
green on SQLite, 1064 in prod, swallowed by a bare except for two days.

Enable by setting `NARRANEXUS_MYSQL_TEST_URL` to a throwaway MySQL DSN:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"


def _parse_mysql_url(url: str) -> dict:
    # Same tiny inline parser as the other *_mysql.py files — no shared helper
    # for this DSN format exists in the codebase yet.
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    body = url[len("mysql://") :]
    creds, _, host_db = body.partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {
        "host": host,
        "port": int(port) if port else 3306,
        "user": user,
        "password": password,
        "database": database,
    }


pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=(
        f"{MYSQL_URL_ENV} not set. These tests validate the team-workspace raw "
        f"SQL against a real MySQL dialect (DISTINCT over a JOIN with an "
        f"aliased ORDER BY, an OR'd IN-subquery, and bound LIMIT parameters). "
        f"Example DSN: mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
    ),
)

_PREFIX = "mysqltws"
TEAM = f"{_PREFIX}_team_1"
OTHER_TEAM = f"{_PREFIX}_team_2"
AGENT = f"{_PREFIX}_agent_a"
MATE = f"{_PREFIX}_agent_b"
USER = f"{_PREFIX}_user"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(_parse_mysql_url(os.environ[MYSQL_URL_ENV]))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    # Shared throwaway DB: clean only what this file created.
    for table, col in (
        ("instance_artifact_history", "artifact_id"),
        ("instance_artifacts", "artifact_id"),
        ("team_files", "file_id"),
        ("team_members", "team_id"),
        ("teams", "team_id"),
    ):
        try:
            await client.execute(
                f"DELETE FROM {table} WHERE {col} LIKE %s", (f"{_PREFIX}%",), fetch=False
            )
        except Exception:  # noqa: BLE001 — teardown must not mask a failure
            pass
    await client.close()


async def _seed_artifact(db, artifact_id, *, team_id, agent_id=AGENT, pinned=True):
    now = datetime.now(timezone.utc)
    await ArtifactRepository(db).create(Artifact(
        artifact_id=artifact_id, agent_id=agent_id, user_id=USER, session_id=None,
        title=artifact_id, kind="text/markdown", pinned=pinned, team_id=team_id,
        file_path=f"p/{artifact_id}.md", size_bytes=1,
        created_at=now, updated_at=now,
    ))


# ── artifact_repository: the three statements this branch added ────────────


@pytest.mark.asyncio
async def test_list_by_team_runs_on_mysql(mysql_client):
    await _seed_artifact(mysql_client, f"{_PREFIX}_a1", team_id=TEAM)
    await _seed_artifact(mysql_client, f"{_PREFIX}_a2", team_id=OTHER_TEAM)

    got = await ArtifactRepository(mysql_client).list_by_team(TEAM)
    assert [a.artifact_id for a in got] == [f"{_PREFIX}_a1"]


@pytest.mark.asyncio
async def test_list_by_team_accepts_a_bound_limit(mysql_client):
    """`LIMIT %s` with a bound parameter, not string interpolation. Drivers
    differ on whether a placeholder is legal there at all."""
    for i in range(3):
        await _seed_artifact(mysql_client, f"{_PREFIX}_b{i}", team_id=TEAM)

    got = await ArtifactRepository(mysql_client).list_by_team(TEAM, limit=2)
    assert len(got) == 2


@pytest.mark.asyncio
async def test_list_pinned_bound_limit_runs_on_mysql(mysql_client):
    """The prompt-block cap added the same bound-LIMIT shape to an existing
    query, so the regression surface is a pre-existing statement."""
    for i in range(3):
        await _seed_artifact(mysql_client, f"{_PREFIX}_c{i}", team_id=None)

    got = await ArtifactRepository(mysql_client).list_pinned(AGENT, limit=2)
    assert len(got) == 2
    assert all(a.team_id is None for a in got), "private surface must stay private"


@pytest.mark.asyncio
async def test_agent_context_union_runs_on_mysql(mysql_client):
    """`(direct predicate) OR (IN (SELECT ...))` plus a bound LIMIT — the
    widest statement the branch adds, and the one whose result is a security
    boundary (membership, not ownership)."""
    await mysql_client.insert("teams", {"team_id": TEAM, "owner_user_id": USER, "name": "T"})
    await mysql_client.insert("team_members", {"team_id": TEAM, "agent_id": AGENT})
    await _seed_artifact(mysql_client, f"{_PREFIX}_d_priv", team_id=None)
    await _seed_artifact(mysql_client, f"{_PREFIX}_d_team", team_id=TEAM, agent_id=MATE)
    await _seed_artifact(mysql_client, f"{_PREFIX}_d_other", team_id=OTHER_TEAM, agent_id=MATE)

    got = await ArtifactRepository(mysql_client).list_for_agent_context(AGENT)
    ids = {a.artifact_id for a in got}
    assert ids == {f"{_PREFIX}_d_priv", f"{_PREFIX}_d_team"}, (
        "own private ∪ teams joined; another team must not leak"
    )


# ── teams route: DISTINCT over a JOIN with an aliased ORDER BY ─────────────


@pytest.mark.asyncio
async def test_artifact_turns_distinct_join_runs_on_mysql(mysql_client):
    """The 3065-shaped statement: SELECT DISTINCT over a JOIN, ordered by a
    column carried through an alias."""
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(mysql_client, f"{_PREFIX}_e1", team_id=TEAM)
    await _seed_artifact(mysql_client, f"{_PREFIX}_e2", team_id=TEAM)
    await _seed_artifact(mysql_client, f"{_PREFIX}_e3", team_id=OTHER_TEAM)
    for aid, evt in ((f"{_PREFIX}_e1", "evt_x"), (f"{_PREFIX}_e2", "evt_x"),
                     (f"{_PREFIX}_e3", "evt_x")):
        await mysql_client.insert("instance_artifact_history", {
            "artifact_id": aid, "agent_id": AGENT, "file_path": "p",
            "action": "created", "event_id": evt,
        })

    got = await _team_artifact_turns(mysql_client, TEAM)
    assert got == {"evt_x": [f"{_PREFIX}_e1", f"{_PREFIX}_e2"]}


@pytest.mark.asyncio
async def test_artifact_turns_skips_null_event_ids_on_mysql(mysql_client):
    """`IS NOT NULL` against a nullable column, verified on the real dialect."""
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(mysql_client, f"{_PREFIX}_f1", team_id=TEAM)
    await mysql_client.insert("instance_artifact_history", {
        "artifact_id": f"{_PREFIX}_f1", "agent_id": AGENT, "file_path": "p",
        "action": "created", "event_id": None,
    })

    assert await _team_artifact_turns(mysql_client, TEAM) == {}


# ── team_files: listing and the dedup probe ───────────────────────────────


async def _seed_file(db, file_id, *, team_id=TEAM, name="report.md", size=10, digest="h"):
    await db.insert("team_files", {
        "file_id": file_id, "team_id": team_id, "owner_user_id": USER,
        "shared_by_agent_id": AGENT, "original_name": name,
        "rel_path": f"p/{file_id}", "size_bytes": size, "content_hash": digest,
    })


@pytest.mark.asyncio
async def test_team_files_listing_runs_on_mysql(mysql_client):
    from narranexus_plugins.teams.routes import _team_files

    await _seed_file(mysql_client, f"{_PREFIX}_g1")
    await _seed_file(mysql_client, f"{_PREFIX}_g2", team_id=OTHER_TEAM)

    rows = await _team_files(mysql_client, TEAM)
    assert [r["file_id"] for r in rows] == [f"{_PREFIX}_g1"]


@pytest.mark.asyncio
async def test_dedup_probe_runs_on_mysql(mysql_client):
    """The (team, name, size) pre-filter — an indexed three-column lookup on
    the write path, so a dialect slip here fails every share, not a read.

    Calls the repository rather than re-typing its SQL here: a copied statement
    drifts from the one that ships, and the previous version of this test ended
    on `assert impl is not None`, which is true by construction.
    """
    from narranexus.platform.repository.team_workspace_repository import TeamFileRepository

    await _seed_file(mysql_client, f"{_PREFIX}_h1", size=42, digest="hash_a")
    rows = await TeamFileRepository(mysql_client).find_by_name_and_size(
        TEAM, "report.md", 42
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_team_files_bound_limit_runs_on_mysql(mysql_client):
    """The agent-facing listing caps with a BOUND LIMIT, and it is the only
    team_files statement that does. Until now the bound-LIMIT evidence came
    entirely from the artifact-side queries."""
    from narranexus.platform.repository.team_workspace_repository import TeamFileRepository

    for i in range(3):
        await _seed_file(mysql_client, f"{_PREFIX}_k{i}", name=f"k{i}.md", digest=f"hk{i}")

    rows = await TeamFileRepository(mysql_client).list_by_team(TEAM, limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_dedup_unique_index_holds_on_mysql(mysql_client):
    """The UNIQUE (team, name, hash) index is what makes concurrent duplicate
    shares safe. SQLite and MySQL both enforce it, but with different error
    types — the constraint has to exist on the real dialect."""
    await _seed_file(mysql_client, f"{_PREFIX}_i1", digest="hash_same")
    with pytest.raises(Exception):
        await _seed_file(mysql_client, f"{_PREFIX}_i2", digest="hash_same")


@pytest.mark.asyncio
async def test_same_name_different_hash_still_inserts_on_mysql(mysql_client):
    """The other half of that index: two genuinely different files sharing a
    name must both survive on MySQL too."""
    await _seed_file(mysql_client, f"{_PREFIX}_j1", digest="hash_a")
    await _seed_file(mysql_client, f"{_PREFIX}_j2", digest="hash_b")

    rows = await mysql_client.execute(
        "SELECT file_id FROM team_files WHERE team_id = %s AND original_name = %s",
        params=(TEAM, "report.md"), fetch=True,
    )
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_history_bulk_delete_runs_on_mysql(mysql_client):
    """`DELETE ... WHERE artifact_id IN (%s, %s, ...)` with a generated
    placeholder list — one statement instead of one per id.

    Covered because this file's own rule is that every hand-written statement
    in the change gets a real dialect run; a generated placeholder count is
    also the shape where an off-by-one between list length and parameter tuple
    shows up as a driver error rather than a wrong result.
    """
    from narranexus.platform.repository.team_workspace_repository import (
        ArtifactHistoryRepository,
    )

    await _seed_artifact(mysql_client, f"{_PREFIX}_m1", team_id=TEAM)
    await _seed_artifact(mysql_client, f"{_PREFIX}_m2", team_id=TEAM)
    await _seed_artifact(mysql_client, f"{_PREFIX}_m3", team_id=TEAM)
    for aid in (f"{_PREFIX}_m1", f"{_PREFIX}_m2", f"{_PREFIX}_m3"):
        await mysql_client.insert("instance_artifact_history", {
            "artifact_id": aid, "agent_id": AGENT, "file_path": "p",
            "action": "created", "event_id": "evt_m",
        })

    repo = ArtifactHistoryRepository(mysql_client)
    await repo.delete_for_artifacts([f"{_PREFIX}_m1", f"{_PREFIX}_m2"])

    left = await mysql_client.execute(
        "SELECT artifact_id FROM instance_artifact_history WHERE artifact_id LIKE %s",
        (f"{_PREFIX}_m%",), fetch=True,
    )
    assert [r["artifact_id"] for r in left] == [f"{_PREFIX}_m3"]


@pytest.mark.asyncio
async def test_history_bulk_delete_tolerates_an_empty_list(mysql_client):
    """An empty id list must not compose `IN ()`, which is a syntax error.

    Asserting a surviving row as well as the absence of an exception: "did not
    raise" alone cannot tell a correct no-op apart from a statement that ran
    and deleted something it should not have.
    """
    from narranexus.platform.repository.team_workspace_repository import (
        ArtifactHistoryRepository,
    )

    await _seed_artifact(mysql_client, f"{_PREFIX}_n1", team_id=TEAM)
    await mysql_client.insert("instance_artifact_history", {
        "artifact_id": f"{_PREFIX}_n1", "agent_id": AGENT, "file_path": "p",
        "action": "created", "event_id": "evt_n",
    })

    await ArtifactHistoryRepository(mysql_client).delete_for_artifacts([])

    left = await mysql_client.execute(
        "SELECT artifact_id FROM instance_artifact_history WHERE artifact_id = %s",
        (f"{_PREFIX}_n1",), fetch=True,
    )
    assert len(left) == 1, "an empty list must delete nothing at all"
