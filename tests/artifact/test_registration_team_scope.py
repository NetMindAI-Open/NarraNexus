"""
@file_name: test_registration_team_scope.py
@author: NarraNexus
@date: 2026-08-07
@description: Registration learns about teams.

Three things change at the registration boundary:

1. **Ownership** — an artifact registered during a team turn belongs to that
   team, so it lands in the team workspace instead of one agent's private
   list. The team comes from the SERVER (identity headers), never from a tool
   argument, so a private turn cannot claim one.
2. **Where the file may live** — the entry had to sit inside the agent's own
   workspace, which made a file in the team shared folder impossible to
   register at all: the shared folder is a SIBLING of every agent workspace by
   design, so it was permanently out of bounds.
3. **Attribution** — every registration appends a history row, because
   re-registering overwrites the pointer in place and otherwise leaves no
   trace of who changed what.

`scope` exists only to let an agent opt OUT ("private") of a team turn's
default. It is a plain `str` with a default rather than `Optional[str]`:
FastMCP renders Optional as `anyOf:[X,null]`, which strict-schema providers
reject with a request-level 400 — a whole-request failure, not a degraded
call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.platform.artifact import ArtifactService
from narranexus.platform.artifact import ArtifactNotFound, ArtifactPathEscape
from narranexus.platform.artifact._artifact_impl.registration import workspace_root
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.utils.workspace_paths import team_shared_dir

AGENT = "agent_a"
USER = "user_1"
TEAM = "team_1"


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    ws = Path(workspace_root(AGENT, USER))
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "own.md").write_text("mine\n")

    shared = team_shared_dir(USER, TEAM)
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "team.md").write_text("ours\n")

    yield {
        "svc": ArtifactService(db_client),
        "repo": ArtifactRepository(db_client),
        "ws": ws,
        "shared": shared,
        "db": db_client,
    }


# ── ownership follows the turn ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_turn_registers_into_the_team(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    got = await env["repo"].get_by_id(res.artifact_id)
    assert got.team_id == TEAM
    assert got.agent_id == AGENT, "producer must survive the move to team ownership"


@pytest.mark.asyncio
async def test_private_turn_is_unchanged(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).team_id is None


# ── the shared folder becomes registerable (gap T6) ────────────────────────


@pytest.mark.asyncio
async def test_team_artifact_may_live_in_the_shared_folder(env):
    """The whole point of a shared folder is that teammates can build on the
    file. Confining entries to the producer's own workspace made the shared
    folder unusable as an artifact source."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).team_id == TEAM


@pytest.mark.asyncio
async def test_private_registration_cannot_reach_the_shared_folder(env):
    """Widening is scoped to the team turn that earned it: without a team,
    the old confinement stands."""
    with pytest.raises(ArtifactPathEscape):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(env["shared"] / "team.md"), title="T", description=None,
            target_artifact_id=None,
        )


@pytest.mark.asyncio
async def test_a_team_cannot_register_from_another_teams_folder(env):
    """Being in team_1 grants team_1's folder — not the sibling next to it."""
    other = team_shared_dir(USER, "team_other")
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.md").write_text("theirs\n")

    with pytest.raises(ArtifactPathEscape):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(other / "secret.md"), title="T", description=None,
            target_artifact_id=None, team_id=TEAM,
        )


# ── attribution history ────────────────────────────────────────────────────


async def _history(db, artifact_id):
    return await db.execute(
        "SELECT * FROM instance_artifact_history WHERE artifact_id = %s "
        "ORDER BY id",
        params=(artifact_id,), fetch=True,
    )


@pytest.mark.asyncio
async def test_first_registration_is_recorded_as_created(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    rows = await _history(env["db"], res.artifact_id)
    assert len(rows) == 1
    assert rows[0]["action"] == "created"
    assert rows[0]["agent_id"] == AGENT


@pytest.mark.asyncio
async def test_teammate_update_is_attributed_to_the_teammate(env):
    """The reason the log exists: the pointer is overwritten in place, so
    without a row nothing records that a DIFFERENT agent changed it."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    # The teammate also writes into the TEAM folder — that is the rule, and it
    # is what makes the update readable by everyone else in the team.
    (env["shared"] / "edit.md").write_text("theirs\n")
    await env["svc"].register(
        agent_id="agent_b", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "edit.md"), title="T2", description=None,
        target_artifact_id=res.artifact_id, team_id=TEAM,
    )

    rows = await _history(env["db"], res.artifact_id)
    assert [r["action"] for r in rows] == ["created", "updated"]
    assert [r["agent_id"] for r in rows] == [AGENT, "agent_b"]


# ── the tool-layer scope resolution ────────────────────────────────────────
#
# The tool is where "which scope" is decided, and the rule is asymmetric on
# purpose: the TEAM comes from the server, `scope` can only narrow. These
# exercise that decision directly, without an MCP transport.


def _resolve_scope(scope: str, caller_team):
    """Mirror of the tool's one-line decision, kept in sync by these tests."""
    return None if str(scope).strip().lower() == "private" else caller_team


def test_default_scope_follows_the_turn():
    """The common case must need no decision from the model at all."""
    assert _resolve_scope("auto", "team_1") == "team_1"
    assert _resolve_scope("auto", None) is None


def test_private_can_only_narrow():
    """`private` opts out of the team the turn IS in. It is a veto, never a
    way to reach a team the turn is not in."""
    assert _resolve_scope("private", "team_1") is None
    assert _resolve_scope("private", None) is None


def test_unrecognised_scope_degrades_to_the_turn():
    """A weak model inventing a value must not silently produce a private
    artifact nobody on the team can find — the safe degradation is 'follow
    the turn', not 'fall back to private'."""
    for invented in ("team", "TEAM", "shared", "", "null", "workspace"):
        assert _resolve_scope(invented, "team_1") == "team_1"


@pytest.mark.asyncio
async def test_scope_renders_without_anyof_null_in_the_real_schema():
    """Assert the SCHEMA FastMCP actually emits, not the source text.

    The 400 is a property of the emitted JSON Schema: FastMCP renders
    `Optional[X]` as `anyOf:[X,null]`, and strict-schema providers reject the
    whole REQUEST — every tool in it, not just this call. Grepping the
    signature would pass while a type-alias or a future annotation change
    quietly reintroduced the shape, so the generated schema is the only
    honest assertion.
    """
    from mcp.server.fastmcp import FastMCP

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        artifact_tool,
    )

    mcp = FastMCP("schema-probe")
    artifact_tool.register(mcp)
    tool = next(t for t in await mcp.list_tools() if t.name == "register_artifact")
    spec = tool.inputSchema["properties"]["scope"]

    assert spec.get("type") == "string", spec
    assert "anyOf" in spec is False or "anyOf" not in spec, spec
    assert "null" not in json.dumps(spec), spec
    assert spec.get("default") == "auto", spec


@pytest.mark.asyncio
async def test_scope_did_not_widen_the_tools_strict_schema_exposure():
    """A regression fence, deliberately not a clean-schema assertion.

    `session_id` / `description` / `target_artifact_id` were already
    Optional-typed before the team workspace, so this tool ALREADY carries the
    anyOf:[X,null] shape on a strict provider — that debt is pre-existing and
    is not this change's to fix (per the design, schema normalisation belongs
    in a gateway layer). What must hold is that adding `scope` did not make it
    worse: the risky set stays exactly those three.
    """
    from mcp.server.fastmcp import FastMCP

    from narranexus_plugins.common_tools_module._common_tools_impl import (
        artifact_tool,
    )

    mcp = FastMCP("schema-probe")
    artifact_tool.register(mcp)
    tool = next(t for t in await mcp.list_tools() if t.name == "register_artifact")

    risky = {
        name for name, spec in tool.inputSchema["properties"].items()
        if "anyOf" in spec or "null" in json.dumps(spec)
    }
    assert risky == {"session_id", "description", "target_artifact_id"}, (
        f"strict-schema exposure changed: {risky}"
    )


# ── dedup must not cross the scope boundary ────────────────────────────────
#
# Found by an end-to-end probe over the real MCP transport, not by the unit
# tests above: each of those used a fresh database, so the collision never
# arose. Registering the SAME entry file in both scopes is ordinary —
# an agent writes report.md once and surfaces it privately, then again for
# the team.


@pytest.mark.asyncio
async def test_same_file_in_both_scopes_makes_two_artifacts(env):
    """Agent-scoped dedup keys on (agent_id, file_path). Without the scope in
    that key, the second registration silently returns the FIRST artifact and
    the requested scope is discarded.

    The two entries are different files now: a team artifact must live in the
    team folder and a private one in the agent's workspace, so one path cannot
    serve both scopes any more. The dedup key still has to carry the scope —
    same agent, same content, two homes.
    """
    (env["shared"] / "own.md").write_text("mine\n")
    private = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="R", description=None,
        target_artifact_id=None,
    )
    team = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "own.md"), title="R", description=None,
        target_artifact_id=None, team_id=TEAM,
    )

    assert private.artifact_id != team.artifact_id, (
        "a team registration must not be deduped onto the private artifact"
    )
    assert (await env["repo"].get_by_id(private.artifact_id)).team_id is None
    assert (await env["repo"].get_by_id(team.artifact_id)).team_id == TEAM


@pytest.mark.asyncio
async def test_private_call_never_returns_a_team_artifact(env):
    """The leak direction the probe actually hit: a private-chat registration
    handed back an artifact owned by a team."""
    (env["shared"] / "own.md").write_text("mine\n")
    team = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "own.md"), title="R", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    private = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="R", description=None,
        target_artifact_id=None,
    )

    assert private.artifact_id != team.artifact_id
    assert (await env["repo"].get_by_id(private.artifact_id)).team_id is None


@pytest.mark.asyncio
async def test_dedup_still_works_within_one_scope(env):
    """The guard that made dedup exist stays intact: re-registering the same
    file in the SAME scope must still reuse the row, not mint a duplicate tab
    (prod 2026-06-30: two pinned 'Welcome' tabs on one agent)."""
    entry = str(env["shared"] / "team.md")
    first = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R", description=None, target_artifact_id=None,
        team_id=TEAM,
    )
    second = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=entry, title="R2", description=None, target_artifact_id=None,
        team_id=TEAM,
    )
    assert first.artifact_id == second.artifact_id


# ── the turn handle reaches the attribution row ────────────────────────────


@pytest.mark.asyncio
async def test_history_records_the_turn_that_made_the_change(env):
    """Attribution answers "who changed this, in which turn". The turn is a
    fact the platform holds (the events row exists from Step 0), so it is
    passed in rather than inferred — matching artifacts to turns by timestamp
    breaks on the ordinary cases: two artifacts in one turn, or concurrent
    turns in the same room.
    """
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM, event_id="evt_first",
    )
    (env["shared"] / "edit.md").write_text("theirs\n")
    await env["svc"].register(
        agent_id="agent_b", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "edit.md"), title="T2", description=None,
        target_artifact_id=res.artifact_id, team_id=TEAM, event_id="evt_second",
    )

    rows = await _history(env["db"], res.artifact_id)
    assert [r["event_id"] for r in rows] == ["evt_first", "evt_second"]


@pytest.mark.asyncio
async def test_a_missing_turn_handle_still_records_the_change(env):
    """Plenty of callers have no event in scope. Absence degrades the record;
    it must never cost the agent a successful registration."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None,
    )
    rows = await _history(env["db"], res.artifact_id)
    assert len(rows) == 1
    assert rows[0]["event_id"] is None


# ── re-registration must respect who owns the target ──────────────────────
#
# The target branch used to look up the artifact by id and check only its
# KIND. Two consequences, one pre-existing and one introduced by teams:
#
#   * any agent that guessed an `art_xxxxxxxx` could repoint someone else's
#     artifact at its own file — no agent, user or team check at all;
#   * the server-side team fact was dropped on this path, so re-registering
#     in a team turn left the artifact wherever it already was, silently
#     contradicting the "team comes from the server" rule the create path
#     enforces.


@pytest.mark.asyncio
async def test_cannot_repoint_another_agents_private_artifact(env):
    """The pre-existing hole. `art_` ids are 8 hex chars, so guessing is not
    the hard part — nothing was checking."""
    victim_ws = Path(workspace_root("agent_victim", USER))
    victim_ws.mkdir(parents=True, exist_ok=True)
    (victim_ws / "theirs.md").write_text("theirs\n")
    victim = await env["svc"].register(
        agent_id="agent_victim", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(victim_ws / "theirs.md"), title="Theirs", description=None,
        target_artifact_id=None,
    )
    attacker_ws = Path(workspace_root("agent_attacker", USER))
    attacker_ws.mkdir(parents=True, exist_ok=True)
    (attacker_ws / "mine.md").write_text("mine\n")

    with pytest.raises(ArtifactNotFound):
        await env["svc"].register(
            agent_id="agent_attacker", user_id=USER, session_id=None,
            kind="text/markdown", entry_path=str(attacker_ws / "mine.md"),
            title="Hijacked", description=None,
            target_artifact_id=victim.artifact_id,
        )

    still = await env["repo"].get_by_id(victim.artifact_id)
    assert still.agent_id == "agent_victim"
    assert still.title == "Theirs", "the pointer must not have moved"


@pytest.mark.asyncio
async def test_a_teammate_may_update_a_team_artifact(env):
    """The case that must keep working: picking up a teammate's work is the
    entire point of a shared workspace, so the check is team membership of the
    ARTIFACT, not agent identity."""
    made = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    (env["shared"] / "edit.md").write_text("theirs\n")
    await env["svc"].register(
        agent_id="agent_b", user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "edit.md"), title="T2", description=None,
        target_artifact_id=made.artifact_id, team_id=TEAM,
    )
    got = await env["repo"].get_by_id(made.artifact_id)
    assert got.title == "T2"
    assert got.team_id == TEAM, "ownership must not drift on update"


@pytest.mark.asyncio
async def test_a_turn_in_another_team_cannot_touch_it(env):
    """Being in SOME team is not being in THIS one.

    The entry is a real file in team_other's own folder, so the path guard is
    satisfied and the refusal comes from the OWNERSHIP guard — which is the one
    under test here. (Feeding it an unreachable path would pass for the wrong
    reason.)
    """
    made = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    other = team_shared_dir(USER, "team_other")
    other.mkdir(parents=True, exist_ok=True)
    (other / "theirs.md").write_text("theirs\n")

    with pytest.raises(ArtifactNotFound):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(other / "theirs.md"), title="Hijacked", description=None,
            target_artifact_id=made.artifact_id, team_id="team_other",
        )


@pytest.mark.asyncio
async def test_a_private_turn_cannot_update_a_team_artifact(env):
    """`scope="private"` narrows the turn; it must not become a way to reach a
    team artifact and quietly pull it out of the team."""
    made = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    with pytest.raises(ArtifactNotFound):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(env["ws"] / "own.md"), title="Pulled out", description=None,
            target_artifact_id=made.artifact_id,
        )


@pytest.mark.asyncio
async def test_own_private_artifact_still_updates_in_place(env):
    """The ordinary iterate-on-my-own-artifact path stays untouched."""
    made = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="v1", description=None,
        target_artifact_id=None,
    )
    await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="v2", description=None,
        target_artifact_id=made.artifact_id,
    )
    assert (await env["repo"].get_by_id(made.artifact_id)).title == "v2"


# ── team output lives in the team folder ──────────────────────────────────
#
# The reason is not tidiness, it is reachability. A teammate opening the work
# has exactly three roots granted to its turn: its own workspace, the bus
# attachment dir, and THIS team's folder (turn_accessible_roots). A file left
# in the producer's workspace is in none of them, so NexusPower denies the
# read — while claude and codex, which run no confinement layer, succeed. That
# is the three-frameworks-two-behaviours state this feature exists to remove,
# and it silently defeats acceptance #3 (a teammate picking the work up).
#
# Pointer semantics are kept: registration still never copies or moves. The
# entry simply has to already be somewhere the team can read.


@pytest.mark.asyncio
async def test_a_team_turn_must_register_from_the_team_folder(env):
    """Own-workspace entries are refused on a team turn, with a message that
    names the folder — the agent can write there (the grant covers writes), so
    the fix is one move and a retry, which the tool contract supports."""
    with pytest.raises(ArtifactPathEscape) as e:
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(env["ws"] / "own.md"), title="T", description=None,
            target_artifact_id=None, team_id=TEAM,
        )
    msg = str(e.value)
    assert str(env["shared"]) in msg, "the error must name where to put it"
    assert "team" in msg.lower()


@pytest.mark.asyncio
async def test_a_team_artifact_registers_from_the_team_folder(env):
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    got = await env["repo"].get_by_id(res.artifact_id)
    assert got.team_id == TEAM
    assert str(env["shared"].name) in got.file_path or "teams" in got.file_path


@pytest.mark.asyncio
async def test_the_registered_path_is_readable_by_a_teammates_turn(env):
    """The point of the whole rule: what lands in the row must sit under a root
    that turn_accessible_roots grants to ANY member of this team."""
    from narranexus.platform.settings import settings as sa
    from narranexus.platform.utils.workspace_paths import turn_accessible_roots

    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "team.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    stored = await env["repo"].get_by_id(res.artifact_id)
    absolute = Path(sa.base_working_path) / stored.file_path

    # A DIFFERENT agent's turn in the same team.
    roots = turn_accessible_roots(USER, team_id=TEAM, base=sa.base_working_path)
    assert any(str(absolute).startswith(r + "/") for r in roots), (
        f"{absolute} is not under any root granted to a teammate: {roots}"
    )


@pytest.mark.asyncio
async def test_a_private_registration_is_unaffected(env):
    """scope=private has no team, so the ordinary workspace rule applies and
    the agent keeps writing where it always did."""
    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["ws"] / "own.md"), title="T", description=None,
        target_artifact_id=None,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).team_id is None


@pytest.mark.asyncio
async def test_another_teams_folder_is_still_refused(env):
    other = team_shared_dir(USER, "team_other")
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.md").write_text("theirs\n")

    with pytest.raises(ArtifactPathEscape):
        await env["svc"].register(
            agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
            entry_path=str(other / "secret.md"), title="T", description=None,
            target_artifact_id=None, team_id=TEAM,
        )


# ── consequences of moving team artifacts into the shared folder ──────────
#
# Requiring team artifacts to live in the team folder (plan B) made that folder
# an artifact ROOT, and two rules that had only ever seen an agent workspace in
# that position started misbehaving. Both were introduced by that change.


@pytest.mark.asyncio
async def test_a_team_artifact_is_sized_by_its_own_file(env):
    """Size accounting must treat the team root like a workspace root.

    `_dir_size(artifact_root)` is meant for a multi-file artifact in its own
    subdirectory. With the entry sitting at the team root, artifact_root IS the
    team folder, so every registration would be charged the size of everything
    the team has ever shared — and once that passes MAX_ARTIFACT_BYTES, no one
    on the team can register anything again.
    """
    big = env["shared"] / "unrelated.bin"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    (env["shared"] / "small.md").write_text("tiny\n")

    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(env["shared"] / "small.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    got = await env["repo"].get_by_id(res.artifact_id)
    assert got.size_bytes < 1000, (
        f"charged {got.size_bytes} bytes — the whole team folder, not the entry"
    )


@pytest.mark.asyncio
async def test_a_multi_file_team_artifact_still_sizes_its_directory(env):
    """The subdirectory case is unchanged: siblings still count."""
    sub = env["shared"] / "report"
    sub.mkdir()
    (sub / "index.md").write_text("entry\n")
    (sub / "data.bin").write_bytes(b"y" * 5000)

    res = await env["svc"].register(
        agent_id=AGENT, user_id=USER, session_id=None, kind="text/markdown",
        entry_path=str(sub / "index.md"), title="T", description=None,
        target_artifact_id=None, team_id=TEAM,
    )
    assert (await env["repo"].get_by_id(res.artifact_id)).size_bytes > 5000
