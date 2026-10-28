"""
@file_name: test_schema_registry_team_workspace.py
@author: NarraNexus
@date: 2026-08-07
@description: Schema guards for the team shared working-space.

Three registrations, one theme — "the team" finally exists in the data
model instead of only on disk:

1. ``instance_artifacts.team_id`` — artifacts gain the team dimension they
   never had (the table was agent_id/user_id only, which is why a team turn's
   artifact could only ever land in one agent's private list).
2. ``team_files`` — the team shared folder had files on disk and no rows in
   the database, so nothing could enumerate it.
3. ``instance_artifact_history`` — attribution for "who changed this, when",
   which is unanswerable today because re-registering overwrites the pointer
   in place.

Every change is additive (iron rule #6): a nullable column and two new
tables, so ``auto_migrate`` provisions them without touching existing rows,
and ``team_id IS NULL`` keeps the exact pre-existing private semantics.
"""

import pytest

from narranexus.platform.utils.db.schema_registry import TABLES


def _cols(table_name: str) -> dict:
    return {c.name: c for c in TABLES[table_name].columns}


def _index_columns(table_name: str) -> list[list[str]]:
    return [list(i.columns) for i in TABLES[table_name].indexes]


# ── instance_artifacts.team_id ──────────────────────────────────────────────


def test_artifacts_have_a_team_dimension():
    col = _cols("instance_artifacts").get("team_id")
    assert col is not None, "instance_artifacts.team_id missing"
    assert col.sqlite_type and col.mysql_type, "both dialects must be declared"


def test_artifact_team_id_is_nullable():
    """NULL is the private artifact — that is what keeps every pre-existing
    row (and every private-chat query) behaving exactly as before."""
    assert _cols("instance_artifacts")["team_id"].nullable is not False


def test_artifacts_indexed_by_team():
    """The team panel lists a team's artifacts; without an index that is a
    full scan of every artifact in the database."""
    assert any("team_id" in cols for cols in _index_columns("instance_artifacts"))


def test_existing_artifact_columns_untouched():
    """Guard the additive promise: nothing that existed may disappear."""
    names = _cols("instance_artifacts")
    for expected in ("artifact_id", "agent_id", "user_id", "session_id", "pinned", "file_path"):
        assert expected in names


# ── team_files ─────────────────────────────────────────────────────────────


def test_team_files_registered_with_dedup_and_attribution_fields():
    cols = _cols("team_files")
    for expected in (
        "file_id",
        "team_id",
        "owner_user_id",
        "shared_by_agent_id",  # attribution: who put it there
        "original_name",
        "rel_path",
        "size_bytes",
        "content_hash",  # dedup: same name is NOT the same file
        "created_at",
    ):
        assert expected in cols, f"team_files.{expected} missing"


def test_team_files_declares_both_dialects():
    for col in TABLES["team_files"].columns:
        assert col.sqlite_type, f"{col.name} missing sqlite_type"
        assert col.mysql_type, f"{col.name} missing mysql_type"


def test_team_files_dedup_index_is_name_plus_hash():
    """Dedup keys on (team, name, hash): same name with a DIFFERENT hash is a
    different file and must remain storable, so the unique index may not stop
    at the name."""
    uniques = [list(i.columns) for i in TABLES["team_files"].indexes if i.unique]
    assert any(
        set(c) >= {"team_id", "original_name", "content_hash"} for c in uniques
    ), f"expected a unique (team_id, original_name, content_hash) index, got {uniques}"


def test_team_files_has_cheap_prefilter_index():
    """Hashing reads the whole file, so the write path only hashes when
    (team, name, size) already collides — that lookup needs an index or the
    'cheap' pre-filter is a full scan."""
    assert any(
        set(cols) >= {"team_id", "original_name", "size_bytes"}
        for cols in _index_columns("team_files")
    )


def test_team_files_listable_by_team():
    assert any("team_id" in cols for cols in _index_columns("team_files"))


# ── instance_artifact_history ──────────────────────────────────────────────


def test_artifact_history_records_who_and_when():
    cols = _cols("instance_artifact_history")
    for expected in ("artifact_id", "agent_id", "file_path", "created_at"):
        assert expected in cols, f"instance_artifact_history.{expected} missing"


def test_artifact_history_carries_the_turn_handle():
    """`event_id` is this codebase's turn handle (see bus_messages.event_id).
    Nullable: the MCP registration path cannot see it yet."""
    cols = _cols("instance_artifact_history")
    assert "event_id" in cols
    assert cols["event_id"].nullable is not False


def test_artifact_history_indexed_by_artifact():
    assert any(
        "artifact_id" in cols for cols in _index_columns("instance_artifact_history")
    )


def test_retired_versions_table_stays_retired():
    """The content-snapshot table was retired 2026-07-21 for cost. The
    attribution log is deliberately NOT a revival of it — if this starts
    failing, someone re-registered content versioning by accident."""
    assert "instance_artifact_versions" not in TABLES


# ── auto_migrate actually provisions them ──────────────────────────────────
#
# Registering a TableDef and getting a table are different claims: the
# registry test above only proves the declaration exists. These run the real
# migration and then use the tables, which is also what proves both dialect
# strings are valid DDL rather than merely present.


@pytest.mark.asyncio
async def test_auto_migrate_creates_the_new_tables(db_client):
    for table in ("team_files", "instance_artifact_history"):
        rows = await db_client.execute(f"PRAGMA table_info({table})", fetch=True)
        assert rows, f"{table} was not created by auto_migrate"


@pytest.mark.asyncio
async def test_auto_migrate_adds_team_id_to_existing_artifacts_table(db_client):
    rows = await db_client.execute("PRAGMA table_info(instance_artifacts)", fetch=True)
    assert "team_id" in {r["name"] for r in rows}


@pytest.mark.asyncio
async def test_same_name_different_content_can_coexist(db_client):
    """The dedup index must not collapse two different files that happen to
    share a name — that would be a silent destructive write."""
    base = {
        "team_id": "team_1", "owner_user_id": "u1", "shared_by_agent_id": "a1",
        "original_name": "report.html", "size_bytes": 10,
    }
    await db_client.insert("team_files", {**base, "file_id": "f1", "rel_path": "p/f1", "content_hash": "hash_aaa"})
    await db_client.insert("team_files", {**base, "file_id": "f2", "rel_path": "p/f2", "content_hash": "hash_bbb"})

    rows = await db_client.execute(
        "SELECT file_id FROM team_files WHERE team_id = %s AND original_name = %s",
        params=("team_1", "report.html"), fetch=True,
    )
    assert {r["file_id"] for r in rows} == {"f1", "f2"}


@pytest.mark.asyncio
async def test_identical_name_and_content_is_rejected_by_the_index(db_client):
    """A true duplicate (same team + name + hash) must be impossible to
    insert twice, so dedup holds even if two shares race."""
    row = {
        "team_id": "team_1", "owner_user_id": "u1", "shared_by_agent_id": "a1",
        "original_name": "report.html", "size_bytes": 10,
        "rel_path": "p/f1", "content_hash": "hash_same",
    }
    await db_client.insert("team_files", {**row, "file_id": "f1"})
    with pytest.raises(Exception):
        await db_client.insert("team_files", {**row, "file_id": "f2"})
