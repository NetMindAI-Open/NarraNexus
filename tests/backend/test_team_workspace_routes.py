"""
@file_name: test_team_workspace_routes.py
@author: NarraNexus
@date: 2026-08-07
@description: The team workspace's read surface, and its cleanup.

Two routes give the team room the panel it never had (`teams.py` carried no
artifact or file route at all), plus the cleanup that has to keep up with the
two new tables.

The cleanup half is the easier one to get wrong: `_wipe_team_data` deletes the
shared directory from disk, so if the index is not deleted with it the UI
happily lists files that no longer exist — rows pointing at nothing, which is
worse than showing nothing, because the user cannot tell the difference until
a download fails.
"""

from __future__ import annotations

import pytest

from narranexus_plugins.teams.routes import _wipe_team_data
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.schema.team_schema import Team
from narranexus.platform.utils.workspace_paths import team_shared_dir

OWNER = "user_t"
TID = "team_abc"
OTHER_TID = "team_other"


async def _seed_artifact(db, artifact_id, *, team_id, agent_id="agent_a"):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    await ArtifactRepository(db).create(Artifact(
        artifact_id=artifact_id, agent_id=agent_id, user_id=OWNER,
        session_id=None, title=artifact_id, kind="text/markdown", pinned=True,
        team_id=team_id, file_path=f"p/{artifact_id}.md", size_bytes=1,
        created_at=now, updated_at=now,
    ))


async def _seed_file(db, file_id, *, team_id):
    await db.insert("team_files", {
        "file_id": file_id, "team_id": team_id, "owner_user_id": OWNER,
        "shared_by_agent_id": "agent_a", "original_name": f"{file_id}.md",
        "rel_path": f"p/{file_id}.md", "size_bytes": 1, "content_hash": file_id,
    })


# ── cleanup keeps the index and the disk in step ──────────────────────────


@pytest.mark.asyncio
async def test_clearing_files_also_clears_the_index(db_client, monkeypatch, tmp_path):
    """Rows that outlive their files are worse than no rows: the panel lists
    them, and the user only finds out when a download fails."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    d = team_shared_dir(OWNER, TID, str(tmp_path))
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text("x")

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "f2", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    res = await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    assert res["files_removed"] is True
    left = await db_client.execute("SELECT * FROM team_files", fetch=True)
    assert {r["file_id"] for r in left} == {"keep"}, "another team's index must survive"


@pytest.mark.asyncio
async def test_clearing_files_removes_only_this_teams_index(db_client, monkeypatch, tmp_path):
    """Scope is the team, never the owner: another team's index and any
    private work belong to neither this folder nor this switch."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    files = {r["file_id"] for r in
             await db_client.execute("SELECT * FROM team_files", fetch=True)}
    assert files == {"keep"}
    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_private"}


@pytest.mark.asyncio
async def test_clearing_only_chat_leaves_the_workspace_alone(db_client, monkeypatch, tmp_path):
    """The two scopes are independent; wiping chat must not touch output."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_artifact(db_client, "art_team", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=True, clear_files=False)

    assert await db_client.execute("SELECT * FROM team_files", fetch=True)
    assert await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)


# ── the read surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_team_files_returns_only_that_team(db_client):
    from narranexus_plugins.teams.routes import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "nope", team_id=OTHER_TID)

    rows = await _team_files(db_client, TID)
    assert [r["file_id"] for r in rows] == ["f1"]


@pytest.mark.asyncio
async def test_team_files_are_newest_first(db_client):
    from narranexus_plugins.teams.routes import _team_files

    await _seed_file(db_client, "older", team_id=TID)
    await _seed_file(db_client, "newer", team_id=TID)

    rows = await _team_files(db_client, TID)
    assert [r["file_id"] for r in rows] == ["newer", "older"]


# ── which turn produced which artifact ────────────────────────────────────
#
# The chip under a team message needs to know what THAT turn produced. The
# link is the events-row id, carried by both bus_messages and the artifact
# history — a real key, not a timestamp guess, which matters because the
# ordinary cases defeat guessing: one turn registering two artifacts, or two
# agents answering in the same room at once.


async def _seed_history(db, artifact_id, *, event_id, agent_id="agent_a"):
    await db.insert("instance_artifact_history", {
        "artifact_id": artifact_id, "agent_id": agent_id,
        "file_path": "p", "action": "created", "event_id": event_id,
    })


@pytest.mark.asyncio
async def test_turn_map_groups_artifacts_by_event(db_client):
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_artifact(db_client, "art_2", team_id=TID)
    await _seed_history(db_client, "art_1", event_id="evt_a")
    await _seed_history(db_client, "art_2", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {"evt_a": ["art_1", "art_2"]}


@pytest.mark.asyncio
async def test_turn_map_excludes_other_teams(db_client):
    """A chip must never point at another team's work."""
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(db_client, "art_mine", team_id=TID)
    await _seed_artifact(db_client, "art_theirs", team_id=OTHER_TID)
    await _seed_history(db_client, "art_mine", event_id="evt_a")
    await _seed_history(db_client, "art_theirs", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {"evt_a": ["art_mine"]}


@pytest.mark.asyncio
async def test_turn_map_excludes_private_artifacts(db_client):
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(db_client, "art_private", team_id=None)
    await _seed_history(db_client, "art_private", event_id="evt_a")

    assert await _team_artifact_turns(db_client, TID) == {}


@pytest.mark.asyncio
async def test_an_updating_turn_also_gets_a_chip(db_client):
    """Re-registration is how a teammate picks work up, so the turn that
    UPDATED an artifact is exactly the one worth surfacing."""
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_history(db_client, "art_1", event_id="evt_first")
    await db_client.insert("instance_artifact_history", {
        "artifact_id": "art_1", "agent_id": "agent_b", "file_path": "p",
        "action": "updated", "event_id": "evt_second",
    })

    assert await _team_artifact_turns(db_client, TID) == {
        "evt_first": ["art_1"], "evt_second": ["art_1"],
    }


@pytest.mark.asyncio
async def test_rows_without_a_turn_are_skipped(db_client):
    """event_id is nullable — legacy rows and callers with no event in scope
    simply produce no chip rather than a bogus grouping."""
    from narranexus_plugins.teams.routes import _team_artifact_turns

    await _seed_artifact(db_client, "art_1", team_id=TID)
    await _seed_history(db_client, "art_1", event_id=None)

    assert await _team_artifact_turns(db_client, TID) == {}


# ── each switch does what its name says ───────────────────────────────────
#
# This rule was revised twice, and the second revision is the interesting one.
#
# Originally clear_files deleted the team's artifacts, defended by "team
# artifacts point into the very tree being deleted" — false at the time, since
# the producer's own workspace was the first allowed root and content there
# survived. So the cascade was removed.
#
# Requiring team artifacts to live in the team folder then made the original
# claim TRUE, and the cascade correct. What changed is the world, not the
# reasoning: the tests below assert the current rule, and the deleted one that
# asserted "clear_files leaves artifacts alone" was guarding a premise that no
# longer holds.


@pytest.mark.asyncio
async def test_deleting_a_team_takes_its_workspace_with_it(db_client, monkeypatch, tmp_path):
    """Once the team is gone its artifacts are unreachable by EVERY query path:
    the private surfaces exclude them (team_id IS NULL), list_by_team needs a
    team that no longer exists, and the union joins team_members which the
    delete just emptied. Rows that nothing can ever read are the orphan case
    acceptance #7 is about."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_artifact(db_client, "art_other", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)
    await _seed_history(db_client, "art_team", event_id="evt_a")
    await _seed_file(db_client, "f1", team_id=TID)
    await _seed_file(db_client, "keep", team_id=OTHER_TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(
        db_client, team, clear_chat=True, clear_files=True, clear_artifacts=True
    )

    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_other", "art_private"}
    hist = await db_client.execute("SELECT * FROM instance_artifact_history", fetch=True)
    assert hist == []
    files = {r["file_id"] for r in
             await db_client.execute("SELECT * FROM team_files", fetch=True)}
    assert files == {"keep"}


@pytest.mark.asyncio
async def test_clearing_artifacts_alone_keeps_the_files(db_client, monkeypatch, tmp_path):
    """The scopes are independent in both directions."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_file(db_client, "f1", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(
        db_client, team, clear_chat=False, clear_files=False, clear_artifacts=True
    )

    assert await db_client.execute("SELECT * FROM instance_artifacts", fetch=True) == []
    assert len(await db_client.execute("SELECT * FROM team_files", fetch=True)) == 1


# ── timestamps must carry a timezone ──────────────────────────────────────


@pytest.mark.asyncio
async def test_file_timestamps_are_offset_aware(db_client):
    """`_team_files` returned raw DB rows, so `created_at` reached the browser
    as a naive string ('2026-08-07 12:34:56' from datetime('now'), UTC but
    unmarked). Per the ES spec a date-time with no offset is read as LOCAL
    time, so `Date.parse` in the panel showed a file shared moments ago as
    "8h ago" for a UTC+8 user. The artifacts half never had this because it
    goes through the Artifact model, whose parse_dt attaches UTC.
    """
    from narranexus_plugins.teams.routes import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    rows = await _team_files(db_client, TID)

    stamp = rows[0]["created_at"]
    assert isinstance(stamp, str), "serialised for the wire"
    assert stamp.endswith("+00:00") or stamp.endswith("Z"), (
        f"timestamp must name its offset, got {stamp!r}"
    )


@pytest.mark.asyncio
async def test_file_rows_do_not_leak_internal_columns(db_client):
    """`SELECT *` put id / owner_user_id / content_hash into the API shape.
    Owner-only, so not a disclosure — but the wire shape should be chosen, not
    inherited from the table."""
    from narranexus_plugins.teams.routes import _team_files

    await _seed_file(db_client, "f1", team_id=TID)
    row = (await _team_files(db_client, TID))[0]

    assert set(row) == {
        "file_id", "original_name", "rel_path", "mime_type", "category",
        "size_bytes", "shared_by_agent_id", "created_at",
    }


# ── clear_files after plan B ──────────────────────────────────────────────
#
# I2 removed artifact deletion from clear_files, reasoning that a team
# artifact's content lived in the PRODUCER's workspace and survived the folder
# being removed — deleting the row would have destroyed a pointer to a file
# that still existed.
#
# Plan B made that premise false. Team artifacts are now REQUIRED to live in
# the team folder, so rmtree'ing it destroys every one of them. Leaving the
# rows behind means the panel lists artifacts whose content is gone, and heal
# cannot recover them: it never passes team_id, so its re-registration path
# hits the ownership check, and its "pointer still valid" shortcut only looks
# inside the agent workspace.


@pytest.mark.asyncio
async def test_clearing_files_now_takes_the_team_artifacts_with_it(
    db_client, monkeypatch, tmp_path
):
    """The content and the row live and die together now."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)
    d = team_shared_dir(OWNER, TID, str(tmp_path))
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.md").write_text("x")

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_history(db_client, "art_team", event_id="evt_a")
    await _seed_file(db_client, "f1", team_id=TID)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    left = await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)
    assert left == [], "their content was just deleted; the rows cannot survive it"
    hist = await db_client.execute("SELECT * FROM instance_artifact_history", fetch=True)
    assert hist == []


@pytest.mark.asyncio
async def test_clearing_files_still_spares_other_teams_and_private_work(
    db_client, monkeypatch, tmp_path
):
    """The cascade is scoped to this team's folder, which is the only content
    being removed."""
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(tmp_path), raising=False)

    await _seed_artifact(db_client, "art_team", team_id=TID)
    await _seed_artifact(db_client, "art_other", team_id=OTHER_TID)
    await _seed_artifact(db_client, "art_private", team_id=None)

    team = Team(team_id=TID, owner_user_id=OWNER, name="T")
    await _wipe_team_data(db_client, team, clear_chat=False, clear_files=True)

    left = {r["artifact_id"] for r in
            await db_client.execute("SELECT * FROM instance_artifacts", fetch=True)}
    assert left == {"art_other", "art_private"}
