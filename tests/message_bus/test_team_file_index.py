"""
@file_name: test_team_file_index.py
@author: NarraNexus
@date: 2026-08-07
@description: The team shared folder gets an index.

Files were staged to disk under a generated file_id and nothing was written
to the database, so the folder could not be enumerated: agents recited
absolute paths at each other in chat and users had no entry point at all.

De-duplication is the interesting part, and it is asymmetric on purpose:

  same name + same bytes       one file re-shared -> reuse the row
  same name + different bytes  two different files -> keep BOTH

Collapsing the second case would be a silent destructive write — the newer
share would take the older one's place with no way back. Keying dedup on the
name alone does exactly that, which is why the content hash is not optional.

Hashing reads the whole file, so the lookup is gated behind a cheap
(team, name, size) index probe. Note the honest limit of that: a brand-new
file IS still hashed once, because the digest has to be stored for any later
comparison to be possible. The pre-filter keeps a share to ONE read of the
source instead of one per candidate row — it does not make new files free.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus._bus_attachment_impl import stage_path_into_team
from narranexus.platform.utils.workspace_paths import agent_workspace_path

AGENT = "agent_a"
USER = "user_1"
TEAM = "team_1"


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "ws"
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    ws = agent_workspace_path(AGENT, USER, base=str(base))
    ws.mkdir(parents=True, exist_ok=True)
    yield {"db": db_client, "ws": ws, "base": str(base)}


async def _share(env, name: str, content: str):
    src = env["ws"] / name
    src.write_text(content)
    return await stage_path_into_team(
        sender_agent_id=AGENT, owner_user_id=USER, team_id=TEAM,
        ref=str(src), base=env["base"], db=env["db"],
    )


async def _rows(env):
    return await env["db"].execute(
        "SELECT * FROM team_files WHERE team_id = %s ORDER BY id",
        params=(TEAM,), fetch=True,
    )


@pytest.mark.asyncio
async def test_sharing_writes_an_index_row(env):
    """Without a row the folder cannot be listed — that was the whole gap."""
    staged = await _share(env, "report.md", "v1\n")
    assert staged is not None

    rows = await _rows(env)
    assert len(rows) == 1
    assert rows[0]["original_name"] == "report.md"
    assert rows[0]["shared_by_agent_id"] == AGENT
    assert rows[0]["owner_user_id"] == USER
    assert rows[0]["content_hash"]


@pytest.mark.asyncio
async def test_resharing_the_identical_file_reuses_the_row(env):
    """Same name, same bytes: one logical file, one row — and no second copy
    on disk either."""
    first = await _share(env, "report.md", "v1\n")
    second = await _share(env, "report.md", "v1\n")

    assert len(await _rows(env)) == 1
    assert second["file_id"] == first["file_id"]


@pytest.mark.asyncio
async def test_same_name_different_content_keeps_both(env):
    """The case name-only dedup destroys. Two genuinely different files that
    happen to share a name must both survive."""
    first = await _share(env, "report.md", "v1\n")
    second = await _share(env, "report.md", "COMPLETELY DIFFERENT\n")

    rows = await _rows(env)
    assert len(rows) == 2
    assert first["file_id"] != second["file_id"]
    assert len({r["content_hash"] for r in rows}) == 2


@pytest.mark.asyncio
async def test_same_content_different_name_is_not_deduped(env):
    """Names carry meaning: the same bytes filed under two names are two
    entries, not one."""
    await _share(env, "report.md", "same\n")
    await _share(env, "summary.md", "same\n")

    rows = await _rows(env)
    assert {r["original_name"] for r in rows} == {"report.md", "summary.md"}


@pytest.mark.asyncio
async def test_a_share_hashes_the_source_at_most_once(env, monkeypatch):
    """Hashing reads the whole file, so a share must pay for it once, never
    per candidate row.

    Note what is NOT claimed: a brand-new file still gets hashed, because the
    digest has to be STORED for any future comparison to be possible. The
    (team, name, size) pre-filter buys something narrower than "new files are
    never hashed" — it stops the lookup from hashing rows it is comparing
    against, and it keeps the comparison to a single read of the source.
    """
    from narranexus.platform.message_bus import _bus_attachment_impl as impl

    calls = {"n": 0}
    real = impl._content_hash

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(impl, "_content_hash", counting)

    await _share(env, "a.md", "first\n")
    assert calls["n"] == 1, "one read to compute the digest we store"

    # Three rows now collide on (team, name, size); the next share must still
    # hash once, comparing against their STORED digests.
    await _share(env, "b.md", "same-size-1\n")
    await _share(env, "b.md", "same-size-2\n")
    before = calls["n"]
    await _share(env, "b.md", "same-size-3\n")
    assert calls["n"] - before == 1, (
        f"expected a single hash of the source, got {calls['n'] - before}"
    )


@pytest.mark.asyncio
async def test_other_teams_are_not_deduped_against(env):
    """Dedup is per team: the same file shared into two teams is two entries,
    and one team must never reuse another's row."""
    await _share(env, "report.md", "v1\n")
    other = await stage_path_into_team(
        sender_agent_id=AGENT, owner_user_id=USER, team_id="team_2",
        ref=str(env["ws"] / "report.md"), base=env["base"], db=env["db"],
    )
    rows = await env["db"].execute("SELECT * FROM team_files", fetch=True)

    assert len(rows) == 2
    assert {r["team_id"] for r in rows} == {TEAM, "team_2"}
    assert other is not None


@pytest.mark.asyncio
async def test_a_racing_duplicate_share_still_returns_the_existing_row(env, monkeypatch):
    """The dedup probe is check-then-act with no lock, and the index is UNIQUE.
    Two concurrent shares of the same bytes both miss the probe; the second
    insert hits the constraint.

    Swallowing that leaves the file on disk with NO row — invisible in the
    panel and to team_list_files, which is the one outcome the index
    exists to prevent. On collision the write path must re-read the winner and
    return it as the "same content re-shared" case, which is the semantics
    this feature already claims.
    """
    from narranexus.platform.message_bus import _bus_attachment_impl as impl

    first = await _share(env, "report.md", "v1\n")

    # Force the probe to miss, reproducing the race deterministically.
    async def _blind(*_a, **_kw):
        return None

    monkeypatch.setattr(impl, "_find_duplicate", _blind)
    second = await _share(env, "report.md", "v1\n")

    assert second is not None, "a losing racer must not return None"
    assert second["file_id"] == first["file_id"], (
        "the loser must adopt the winner's row rather than vanish"
    )
    assert len(await _rows(env)) == 1
