"""
@file_name: test_bundle_team_bulletin.py
@author: NarraNexus
@date: 2026-08-10
@description: A packaged team arrives with its rules.

A bundle is how a team is handed to someone else. The bulletin is the team's
operating conventions, so a bundle that leaves it behind ships a team that has
forgotten how it works — and the recipient cannot know what was lost, because
the absence looks exactly like a team that never had any rules.

Two things are deliberately NOT carried, and both are asserted:

  * the auto-summary. It describes progress in the exporter's run, so in the
    recipient's install it is a confident description of work that never
    happened there. It regenerates on its own within minutes.
  * `author_id`. Both agent ids and the owner's user id are re-minted on
    import; a stale id would attribute a rule to whoever now holds that id, or
    to nobody. The bundle keeps the SOURCE (so an agent-written rule still
    reads as one) and drops the pointer.
"""

from __future__ import annotations

import pytest

from narranexus.platform.bundle.team_bulletin_transfer import (
    collect_bulletin_for_export,
    write_imported_bulletin,
)
from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)

TEAM = "team_src"
NEW_TEAM = "team_dst"
OWNER = "user_1"


@pytest.fixture
async def repo(db_client):
    return TeamBulletinRepository(db_client)


# ── export ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rules_are_exported(repo, db_client):
    await repo.add(team_id=TEAM, content="use Chinese", source="user", author_id=OWNER)
    await repo.add(team_id=TEAM, content="format v2", source="agent", author_id="agent_a")

    out = await collect_bulletin_for_export(db_client, TEAM)

    assert [e["content"] for e in out] == ["use Chinese", "format v2"]


@pytest.mark.asyncio
async def test_the_source_survives_so_attribution_still_reads(repo, db_client):
    """The recipient should still be able to tell "the team decided this" from
    "the owner decided this" — that is what makes a rule reviewable."""
    await repo.add(team_id=TEAM, content="format v2", source="agent", author_id="agent_a")
    out = await collect_bulletin_for_export(db_client, TEAM)
    assert out[0]["source"] == "agent"


@pytest.mark.asyncio
async def test_author_ids_are_not_exported(repo, db_client):
    """Agent ids and the owner's user id are re-minted on import, so a carried
    id would attribute a rule to whoever now holds it — or to nobody."""
    await repo.add(team_id=TEAM, content="x", source="agent", author_id="agent_a")
    out = await collect_bulletin_for_export(db_client, TEAM)
    assert "author_id" not in out[0]


@pytest.mark.asyncio
async def test_the_auto_summary_is_not_exported(repo, db_client):
    """It describes progress in the exporter's install. In the recipient's it
    would be a confident account of work that never happened there."""
    await repo.add(team_id=TEAM, content="a rule", source="user", author_id=OWNER)
    await repo.upsert_summary(TEAM, "we finished the parser last Tuesday")

    out = await collect_bulletin_for_export(db_client, TEAM)

    assert [e["content"] for e in out] == ["a rule"]


@pytest.mark.asyncio
async def test_the_tier_survives(repo, db_client):
    await repo.add(
        team_id=TEAM,
        content="this task only",
        source="user",
        author_id=OWNER,
        tier="current_task",
    )
    out = await collect_bulletin_for_export(db_client, TEAM)
    assert out[0]["tier"] == "current_task"


@pytest.mark.asyncio
async def test_a_team_with_no_bulletin_exports_nothing(db_client):
    assert await collect_bulletin_for_export(db_client, TEAM) == []


# ── import ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_imported_rules_land_under_the_new_team_id(repo, db_client):
    await write_imported_bulletin(
        db_client, NEW_TEAM, [{"content": "use Chinese", "source": "user", "tier": "long_term"}]
    )

    entries = await repo.list_for_team(NEW_TEAM)
    assert [e.content for e in entries] == ["use Chinese"]
    assert entries[0].team_id == NEW_TEAM


@pytest.mark.asyncio
async def test_an_imported_entry_has_no_stale_author(repo, db_client):
    await write_imported_bulletin(
        db_client, NEW_TEAM, [{"content": "format v2", "source": "agent", "tier": "long_term"}]
    )
    entry = (await repo.list_for_team(NEW_TEAM))[0]
    assert entry.source == "agent"
    assert entry.author_id is None


@pytest.mark.asyncio
async def test_an_import_cannot_smuggle_in_a_summary(repo, db_client):
    """A hand-edited bundle must not be able to plant a permanent "progress"
    paragraph the recipient's worker would then treat as its own slot."""
    await write_imported_bulletin(
        db_client,
        NEW_TEAM,
        [{"content": "planted", "source": "auto_summary", "tier": "long_term"}],
    )
    assert await repo.get_summary(NEW_TEAM) is None


@pytest.mark.asyncio
async def test_an_import_respects_the_entry_ceiling(repo, db_client):
    """A bundle is untrusted input. Without a cap it could hand the recipient a
    bulletin that dwarfs every one of their team turns."""
    from narranexus.platform.schema.team_schema import BULLETIN_MAX_ENTRIES

    payload = [{"content": f"rule {i}", "source": "user", "tier": "long_term"} for i in range(BULLETIN_MAX_ENTRIES * 3)]
    await write_imported_bulletin(db_client, NEW_TEAM, payload)

    usage = await repo.usage(NEW_TEAM)
    assert usage.entry_count <= BULLETIN_MAX_ENTRIES


@pytest.mark.asyncio
async def test_an_import_respects_the_length_ceiling(repo, db_client):
    from narranexus.platform.schema.team_schema import (
        BULLETIN_MAX_ENTRY_CHARS,
        BULLETIN_MAX_TOTAL_CHARS,
    )

    payload = [
        {"content": "z" * (BULLETIN_MAX_ENTRY_CHARS * 4), "source": "user", "tier": "long_term"} for _ in range(5)
    ]
    await write_imported_bulletin(db_client, NEW_TEAM, payload)

    usage = await repo.usage(NEW_TEAM)
    assert usage.total_chars <= BULLETIN_MAX_TOTAL_CHARS


@pytest.mark.asyncio
async def test_a_malformed_entry_is_skipped_not_fatal(repo, db_client):
    """One bad row in a bundle must not abort an otherwise good import — the
    recipient would be left with a half-written team and no way to retry."""
    await write_imported_bulletin(
        db_client,
        NEW_TEAM,
        [
            {"source": "user"},  # no content
            {"content": "   "},  # blank
            {"content": "good one", "source": "user", "tier": "long_term"},
        ],
    )
    assert [e.content for e in await repo.list_for_team(NEW_TEAM)] == ["good one"]


@pytest.mark.asyncio
async def test_importing_nothing_is_a_no_op(repo, db_client):
    await write_imported_bulletin(db_client, NEW_TEAM, [])
    assert await repo.list_for_team(NEW_TEAM) == []


# ── round trip ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_round_trip_preserves_the_rules(repo, db_client):
    """The acceptance criterion: export a team, import it, the rules are there."""
    await repo.add(team_id=TEAM, content="use Chinese", source="user", author_id=OWNER)
    await repo.add(
        team_id=TEAM,
        content="reports to the shared folder",
        source="agent",
        author_id="agent_a",
    )
    await repo.add(
        team_id=TEAM,
        content="this task's input is /x.csv",
        source="user",
        author_id=OWNER,
        tier="current_task",
    )
    await repo.upsert_summary(TEAM, "halfway")

    payload = await collect_bulletin_for_export(db_client, TEAM)
    await write_imported_bulletin(db_client, NEW_TEAM, payload)

    got = await repo.list_for_team(NEW_TEAM)
    assert [(e.content, e.source, e.tier) for e in got] == [
        ("use Chinese", "user", "long_term"),
        ("reports to the shared folder", "agent", "long_term"),
        ("this task's input is /x.csv", "user", "current_task"),
    ]


# ── the wiring ──────────────────────────────────────────────────────────────
#
# A transfer module nobody calls is dead code that passes its own tests. Both
# ends have to be connected for a round trip to mean anything.


def test_the_builder_puts_the_bulletin_in_the_manifest():
    import inspect

    from narranexus.platform.bundle import builder

    src = inspect.getsource(builder)
    assert '"bulletin": await collect_bulletin_for_export(' in src


def test_the_importer_writes_it_under_the_new_team():
    import inspect

    from narranexus.platform.bundle import importer

    src = inspect.getsource(importer)
    assert "write_imported_bulletin(" in src
    # Under the NEW id, not the exporter's — the whole point of the id map.
    call = src[src.index("write_imported_bulletin(") :]
    assert "new_tid" in call[: call.index(")")]


# ── rollback ────────────────────────────────────────────────────────────────


def test_the_import_rollback_sweeps_the_bulletin_table():
    """A failed import must not leave bulletin rows keyed on a team_id it is
    about to delete.

    The generic agent-table sweep only covers tables with an `agent_id` column,
    and team_bulletin_entries has team_id/author_id — so it fell through both
    and left rows no query path could ever reach. That is precisely the orphan
    `_wipe_team_data` argues against, and #259 added its own table to this same
    loop one line above, which is what made the gap visible.

    Asserted on the rollback list rather than by driving a failing import: the
    sweep is a fixed sequence of deletes, and what matters is that this table is
    in it.
    """
    import inspect

    from narranexus.platform.bundle import importer

    src = inspect.getsource(importer)
    rollback = src[src.index("for tid in new_team_ids"):]
    rollback = rollback[: rollback.index('await _del("teams"')]
    assert '_del("team_bulletin_entries", "team_id", tid)' in rollback
