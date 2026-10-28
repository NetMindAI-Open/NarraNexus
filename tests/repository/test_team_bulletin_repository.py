"""
@file_name: test_team_bulletin_repository.py
@author: NarraNexus
@date: 2026-08-10
@description: The bulletin's storage rules — the ones the rest of the feature
assumes and therefore cannot re-check.

Three of these encode decisions that are easy to get wrong later and expensive
to notice:

  * the auto-summary is a SLOT, not a growing list. It enters every team turn's
    prompt, so an accumulating summary reproduces the exact problem the
    bulletin exists to solve — and a bad summary would compound instead of
    being overwritten.
  * the summary does not spend the user's entry budget. Automatic output must
    never be able to push a hand-written rule out of the prompt.
  * budget arithmetic counts what actually reaches the prompt, so an over-long
    entry is refused at the edge rather than silently trimmed. A user who is
    told nothing assumes the whole rule is in force.
"""

from __future__ import annotations

import pytest

from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)

TEAM = "team_1"
OTHER_TEAM = "team_2"


@pytest.fixture
async def repo(db_client):
    return TeamBulletinRepository(db_client)


async def _add(repo, **kw):
    kw.setdefault("team_id", TEAM)
    kw.setdefault("content", "rule")
    kw.setdefault("source", "user")
    kw.setdefault("author_id", "usr_1")
    kw.setdefault("tier", "long_term")
    return await repo.add(**kw)


@pytest.mark.asyncio
async def test_entries_are_scoped_to_their_team(repo):
    await _add(repo, content="ours")
    await _add(repo, team_id=OTHER_TEAM, content="theirs")

    got = await repo.list_for_team(TEAM)
    assert [e.content for e in got] == ["ours"]


@pytest.mark.asyncio
async def test_an_entry_id_is_generated_and_prefixed(repo):
    entry = await _add(repo)
    assert entry.entry_id.startswith("bul_")


@pytest.mark.asyncio
async def test_the_auto_summary_is_one_slot_that_gets_overwritten(repo):
    """Not a growing list. It is in every turn's prompt."""
    await repo.upsert_summary(TEAM, "progress v1")
    await repo.upsert_summary(TEAM, "progress v2")

    summaries = [e for e in await repo.list_for_team(TEAM) if e.source == "auto_summary"]
    assert len(summaries) == 1
    assert summaries[0].content == "progress v2"


@pytest.mark.asyncio
async def test_each_team_keeps_its_own_summary_slot(repo):
    """The slot is per team — overwriting must not be global."""
    await repo.upsert_summary(TEAM, "ours")
    await repo.upsert_summary(OTHER_TEAM, "theirs")

    assert (await repo.get_summary(TEAM)).content == "ours"
    assert (await repo.get_summary(OTHER_TEAM)).content == "theirs"


@pytest.mark.asyncio
async def test_the_summary_does_not_spend_the_user_entry_budget(repo):
    """Automatic output must never crowd out a hand-written rule."""
    await _add(repo, content="a real rule")
    await repo.upsert_summary(TEAM, "x" * 700)

    usage = await repo.usage(TEAM)
    assert usage.entry_count == 1
    assert usage.total_chars == len("a real rule")


@pytest.mark.asyncio
async def test_usage_counts_agent_entries_too(repo):
    """Both writers share one budget: the prompt does not care who wrote it."""
    await _add(repo, content="user rule")
    await _add(repo, content="agent rule", source="agent", author_id="agent_a")

    usage = await repo.usage(TEAM)
    assert usage.entry_count == 2
    assert usage.total_chars == len("user rule") + len("agent rule")


@pytest.mark.asyncio
async def test_an_empty_team_reports_zero_usage(repo):
    """The zero-cost path depends on this being 0, not None."""
    usage = await repo.usage(TEAM)
    assert usage.entry_count == 0
    assert usage.total_chars == 0


@pytest.mark.asyncio
async def test_entries_come_back_oldest_first(repo):
    """Numbered rules in the prompt must keep a stable order between turns —
    an agent told "rule 2" should not find a different rule 2 next turn."""
    for c in ("first", "second", "third"):
        await _add(repo, content=c)

    assert [e.content for e in await repo.list_for_team(TEAM) if e.source == "user"] == [
        "first",
        "second",
        "third",
    ]


@pytest.mark.asyncio
async def test_deleting_by_tier_leaves_the_other_tier_alone(repo):
    """ "Clear the current task" must not take the standing rules with it."""
    await _add(repo, content="standing", tier="long_term")
    await _add(repo, content="this task", tier="current_task")

    removed = await repo.delete_tier(TEAM, "current_task")

    assert removed == 1
    assert [e.content for e in await repo.list_for_team(TEAM)] == ["standing"]


@pytest.mark.asyncio
async def test_clearing_a_tier_does_not_remove_the_summary(repo):
    """The summary belongs to no tier; a tier wipe must not silently drop it."""
    await repo.upsert_summary(TEAM, "progress")
    await _add(repo, content="this task", tier="current_task")

    await repo.delete_tier(TEAM, "current_task")

    assert (await repo.get_summary(TEAM)) is not None


@pytest.mark.asyncio
async def test_deleting_the_team_takes_every_entry(repo):
    await _add(repo)
    await repo.upsert_summary(TEAM, "progress")
    await _add(repo, team_id=OTHER_TEAM)

    await repo.delete_for_team(TEAM)

    assert await repo.list_for_team(TEAM) == []
    assert len(await repo.list_for_team(OTHER_TEAM)) == 1


@pytest.mark.asyncio
async def test_an_entry_can_be_fetched_and_deleted_by_id(repo):
    """The permission checks upstream need to read an entry's owner before
    deciding whether the caller may touch it."""
    entry = await _add(repo, source="agent", author_id="agent_a")

    got = await repo.get(entry.entry_id)
    assert got.author_id == "agent_a"
    assert got.source == "agent"

    assert await repo.delete(entry.entry_id) is True
    assert await repo.get(entry.entry_id) is None
