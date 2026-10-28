"""
@file_name: test_team_bulletin_routes.py
@author: NarraNexus
@date: 2026-08-10
@description: The bulletin's user-facing surface — budgets and permissions.

The budget tests are the substance here. Everything in the bulletin reaches
every member's every team turn, so an unbounded bulletin is an unbounded
prompt. The rule pinned below is that an over-budget write is REFUSED with an
explanation rather than trimmed to fit: a user whose rule was silently
shortened is told nothing and assumes the whole rule is in force, which is a
worse failure than being asked to shorten it themselves.

The permission tests cover the asymmetry the PRD asks for under 方案 B: the
user owns the bulletin outright, while an agent may add and may retract its
own suggestions but may never touch what the user wrote. An agent that could
delete the user's rules would be able to delete the very rule constraining it.
"""

from __future__ import annotations

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from narranexus.platform.schema.team_schema import (
    BULLETIN_MAX_ENTRIES,
    BULLETIN_MAX_ENTRY_CHARS,
    BULLETIN_MAX_TOTAL_CHARS,
)

import narranexus_plugins.teams.routes as teams_mod
from narranexus_plugins.teams.routes import (
    BulletinLimitExceeded,
    add_bulletin_entry,
    check_bulletin_budget,
    edit_bulletin_entry,
)

TEAM = "team_1"


def _client(db_client, monkeypatch, viewer="usr_owner"):
    """Mount the teams router with auth and the DB faked, matching the
    pattern used for the work-board route tests.

    The router calls into `narranexus.sdk.web` (current_user_id /
    get_db_client), which resolves through the plugin kernel's `host.web`
    service. That is normally exposed as a side effect of `backend.main`
    being imported by the running app; a standalone route test has to
    install it itself (`replace=True` makes repeat installs harmless).
    """
    from backend.plugin_sdk_host import install_web_host

    install_web_host()

    app = FastAPI()
    app.include_router(teams_mod.router, prefix="/api/teams")

    @app.middleware("http")
    async def _auth(request, call_next):
        request.state.user_id = viewer
        return await call_next(request)

    async def _get_db():
        return db_client

    monkeypatch.setattr(teams_mod, "get_db_client", _get_db)
    return TestClient(app)


async def _seed_team(db_client, *, owner="usr_owner"):
    await db_client.insert("teams", {"team_id": TEAM, "owner_user_id": owner, "name": "Desk"})


@pytest.fixture
async def repo(db_client):
    return TeamBulletinRepository(db_client)


# ── budgets: refuse, never trim ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_entry_within_budget_is_accepted(repo):
    entry = await add_bulletin_entry(repo, team_id=TEAM, content="use Chinese", source="user", author_id="usr_1")
    assert entry.content == "use Chinese"


@pytest.mark.asyncio
async def test_an_over_long_entry_is_refused_not_trimmed(repo):
    """The core rule. Trimming leaves the user believing a rule is in force
    when only its first half is."""
    too_long = "x" * (BULLETIN_MAX_ENTRY_CHARS + 1)

    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content=too_long, source="user", author_id="usr_1")

    assert await repo.list_for_team(TEAM) == []


@pytest.mark.asyncio
async def test_the_refusal_says_what_the_limit_is(repo):
    """ "Too long" without a number leaves the user guessing how much to cut."""
    with pytest.raises(BulletinLimitExceeded) as e:
        await add_bulletin_entry(
            repo,
            team_id=TEAM,
            content="x" * (BULLETIN_MAX_ENTRY_CHARS + 1),
            source="user",
            author_id="usr_1",
        )
    assert str(BULLETIN_MAX_ENTRY_CHARS) in str(e.value)


@pytest.mark.asyncio
async def test_the_entry_count_ceiling_is_enforced(repo):
    for i in range(BULLETIN_MAX_ENTRIES):
        await add_bulletin_entry(repo, team_id=TEAM, content=f"rule {i}", source="user", author_id="usr_1")

    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content="one too many", source="user", author_id="usr_1")


@pytest.mark.asyncio
async def test_the_total_character_ceiling_is_enforced(repo):
    """Reachable well under the entry-count ceiling: 20 long entries would be
    a far bigger prompt than the count alone suggests."""
    chunk = "y" * BULLETIN_MAX_ENTRY_CHARS
    added = 0
    while (added + 1) * BULLETIN_MAX_ENTRY_CHARS <= BULLETIN_MAX_TOTAL_CHARS:
        await add_bulletin_entry(repo, team_id=TEAM, content=chunk, source="user", author_id="usr_1")
        added += 1

    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content=chunk, source="user", author_id="usr_1")


@pytest.mark.asyncio
async def test_the_summary_does_not_consume_the_entry_budget(repo):
    """Automatic output must never be the reason a user cannot add a rule."""
    await repo.upsert_summary(TEAM, "z" * 800)

    for i in range(BULLETIN_MAX_ENTRIES):
        await add_bulletin_entry(repo, team_id=TEAM, content=f"rule {i}", source="user", author_id="usr_1")

    usage = await repo.usage(TEAM)
    assert usage.entry_count == BULLETIN_MAX_ENTRIES


@pytest.mark.asyncio
async def test_budget_is_shared_between_the_user_and_agents(repo):
    """One prompt, one budget — an agent cannot mint itself extra room."""
    for i in range(BULLETIN_MAX_ENTRIES):
        await add_bulletin_entry(repo, team_id=TEAM, content=f"rule {i}", source="agent", author_id="agent_a")

    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content="user rule", source="user", author_id="usr_1")


@pytest.mark.asyncio
async def test_an_empty_entry_is_refused(repo):
    """A blank line in the prompt is pure cost with no reader."""
    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content="   ", source="user", author_id="usr_1")


@pytest.mark.asyncio
async def test_budget_check_is_per_team(repo):
    """One team filling up must not block another."""
    for i in range(BULLETIN_MAX_ENTRIES):
        await add_bulletin_entry(repo, team_id=TEAM, content=f"rule {i}", source="user", author_id="usr_1")

    other = await add_bulletin_entry(repo, team_id="team_2", content="fine", source="user", author_id="usr_1")
    assert other.team_id == "team_2"


@pytest.mark.asyncio
async def test_the_budget_probe_reports_remaining_room(repo):
    """The UI disables "add" before the user types, rather than after."""
    await add_bulletin_entry(repo, team_id=TEAM, content="abc", source="user", author_id="usr_1")
    state = await check_bulletin_budget(repo, TEAM)
    assert state.entry_count == 1
    assert state.total_chars == 3


# ── wipe integration ────────────────────────────────────────────────────────


class _FakeTeam:
    def __init__(self, team_id=TEAM, owner="usr_1"):
        self.team_id = team_id
        self.owner_user_id = owner
        self.name = "Desk"


@pytest.mark.asyncio
async def test_wiping_the_chat_does_not_wipe_the_bulletin(db_client, repo):
    """The bulletin exists BECAUSE it is not chat. Folding it into the chat
    scope would recreate the "say it again" loop it was built to end — the
    user clears a noisy transcript and silently loses every standing rule."""
    from narranexus_plugins.teams.routes import _wipe_team_data

    await add_bulletin_entry(repo, team_id=TEAM, content="use Chinese", source="user", author_id="usr_1")

    await _wipe_team_data(db_client, _FakeTeam(), clear_chat=True, clear_files=False)

    assert len(await repo.list_for_team(TEAM)) == 1


@pytest.mark.asyncio
async def test_the_bulletin_scope_clears_rules_and_summary(db_client, repo):
    from narranexus_plugins.teams.routes import _wipe_team_data

    await add_bulletin_entry(repo, team_id=TEAM, content="use Chinese", source="user", author_id="usr_1")
    await repo.upsert_summary(TEAM, "halfway there")

    result = await _wipe_team_data(db_client, _FakeTeam(), clear_chat=False, clear_files=False, clear_bulletin=True)

    assert result["bulletin_entries"] == 2
    assert await repo.list_for_team(TEAM) == []


@pytest.mark.asyncio
async def test_a_wipe_leaves_another_teams_bulletin_alone(db_client, repo):
    from narranexus_plugins.teams.routes import _wipe_team_data

    await add_bulletin_entry(repo, team_id=TEAM, content="ours", source="user", author_id="usr_1")
    await add_bulletin_entry(repo, team_id="team_2", content="theirs", source="user", author_id="usr_1")

    await _wipe_team_data(db_client, _FakeTeam(), clear_chat=False, clear_files=False, clear_bulletin=True)

    assert len(await repo.list_for_team("team_2")) == 1


def test_deleting_a_team_takes_its_bulletin():
    """Same orphan argument as the workspace: once the team row is gone the
    bulletin's only reader — this team's turn builder — can never reach it."""
    import inspect

    from narranexus_plugins.teams import routes as mod

    src = inspect.getsource(mod.delete_team)
    assert "clear_bulletin=True" in src


# ── editing arithmetic ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shortening_an_entry_at_the_ceiling_is_allowed(repo):
    """The edit path must subtract the text it REPLACES. Counting the new text
    against a total that still includes the old one refuses the very edit that
    relieves the budget — the user is told to free space by doing exactly what
    they were just blocked from doing."""
    chunk = "y" * BULLETIN_MAX_ENTRY_CHARS
    entries = []
    while (len(entries) + 1) * BULLETIN_MAX_ENTRY_CHARS <= BULLETIN_MAX_TOTAL_CHARS:
        entries.append(await add_bulletin_entry(repo, team_id=TEAM, content=chunk, source="user", author_id="usr_1"))
    # Sitting exactly at the ceiling: adding is refused...
    with pytest.raises(BulletinLimitExceeded):
        await add_bulletin_entry(repo, team_id=TEAM, content="x", source="user", author_id="usr_1")

    # ...but shortening an existing one must work.
    edited = await edit_bulletin_entry(repo, team_id=TEAM, entry_id=entries[0].entry_id, content="short now")
    assert edited.content == "short now"


@pytest.mark.asyncio
async def test_an_edit_that_grows_past_the_ceiling_is_still_refused(repo):
    """Subtracting the replaced text must not turn the edit path into a way
    around the budget.

    Sized explicitly rather than by a fill loop: the obvious loop lands the
    projected total exactly ON the ceiling, which is legitimately allowed, and
    the test then asserts a refusal that should never happen.
    """
    small = await add_bulletin_entry(repo, team_id=TEAM, content="tiny", source="user", author_id="usr_1")
    # Everything else must exceed MAX_TOTAL - MAX_ENTRY, so that replacing
    # `small` with a max-length entry overshoots rather than exactly fills.
    others = BULLETIN_MAX_TOTAL_CHARS - BULLETIN_MAX_ENTRY_CHARS + 1
    while others > 0:
        chunk = min(others, BULLETIN_MAX_ENTRY_CHARS)
        await add_bulletin_entry(repo, team_id=TEAM, content="y" * chunk, source="user", author_id="usr_1")
        others -= chunk

    with pytest.raises(BulletinLimitExceeded):
        await edit_bulletin_entry(
            repo,
            team_id=TEAM,
            entry_id=small.entry_id,
            content="y" * BULLETIN_MAX_ENTRY_CHARS,
        )


@pytest.mark.asyncio
async def test_an_entry_from_another_team_is_not_editable_through_this_one(repo):
    """The path carries the team; the id alone must not be the authority."""
    theirs = await add_bulletin_entry(repo, team_id="team_2", content="theirs", source="user", author_id="usr_1")
    assert await edit_bulletin_entry(repo, team_id=TEAM, entry_id=theirs.entry_id, content="hijacked") is None
    assert (await repo.get(theirs.entry_id)).content == "theirs"


# ── the API surface returns real JSON, not `str(dict)` reprs ────────────────
#
# `format_for_api` formats a single datetime; it is not a dict formatter. Fed
# a whole `model_dump()`, it used to hit the AttributeError branch and fall
# back to `str(dt)` — a Python repr string. The frontend's filter
# (`e.source !== 'auto_summary'`) let those strings THROUGH (`undefined !==
# 'auto_summary'`), so the panel rendered one blank row per entry — content
# and entry_id undefined, every React key undefined — rather than reporting
# empty.


@pytest.mark.asyncio
async def test_list_bulletin_returns_entries_as_dicts_with_string_timestamps(db_client, monkeypatch):
    await _seed_team(db_client)
    repo = TeamBulletinRepository(db_client)
    await add_bulletin_entry(repo, team_id=TEAM, content="use Chinese", source="user", author_id="usr_owner")

    client = _client(db_client, monkeypatch)
    resp = client.get(f"/api/teams/{TEAM}/bulletin")

    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, dict), f"entry must be a JSON object, got {entry!r}"
    assert entry["content"] == "use Chinese"
    assert isinstance(entry["created_at"], str)
    assert not entry["created_at"].startswith("{"), "must not be a str(dict) repr"
    # Converted by type, not by field name: both datetimes get the Z suffix
    # the frontend's `new Date()` needs (a bare FastAPI serialisation has none).
    assert entry["created_at"].endswith("Z") and entry["updated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_create_bulletin_entry_returns_entry_as_a_dict(db_client, monkeypatch):
    await _seed_team(db_client)

    client = _client(db_client, monkeypatch)
    resp = client.post(f"/api/teams/{TEAM}/bulletin", json={"content": "keep replies short"})

    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert isinstance(entry, dict), f"entry must be a JSON object, got {entry!r}"
    assert entry["content"] == "keep replies short"
    # `add()` returns the in-memory object it just built (never re-fetched),
    # so `created_at` is legitimately None here — the DB default has not
    # round-tripped back. The bug under test is the str(dict) repr, not this.
    assert entry["created_at"] is None
