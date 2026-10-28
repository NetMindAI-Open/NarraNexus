"""
@file_name: test_turn_user_id_is_the_owner.py
@author: NarraNexus
@date: 2026-08-15
@description: Whoever triggered the turn, `ctx.user_id` is the agent's OWNER.

`run()` takes a `user_id` argument that every trigger fills with whatever it
has: the web chat passes the person typing, the bus trigger passes the PEER
AGENT that sent the message (or `team_<id>` for a leader patrol), Lark passes
the Lark sender. Immediately after the agent row is loaded, that argument is
replaced with `agents.created_by` — and everything downstream reads the
replacement.

Why this is load-bearing rather than tidy-up. The replacement decides:

* which ChatModule instance the turn binds to (instances are keyed
  `(narrative_id, user_id)`), hence WHICH CONVERSATION HISTORY it loads;
* the agent's workspace directory (`{base}/{user_id}/…`) and the per-user
  shared tree the turn may reach;
* which per-user Executor is provisioned for the turn;
* `self._current_user_id`, which the callback path falls back to when a
  caller does not pass a user of its own.

Without the replacement, a team room would shard all of those per speaker:
every teammate that @mentioned the agent would get its own instance with its
own history, its own workspace, its own executor — and the agent would meet
each of them cold, every time. That is exactly the "cold start" a team-room
design pass suspected was still live; it is not, and this file is why.

The invariant had no test at all before this one, in a codebase where four
separate consumers silently depend on it. It goes through the real `run()`
via the `capture_run_context` fixture — no steps execute.
"""
from __future__ import annotations

import pytest

OWNER = "usr_real_owner"
AGENT = "agent_under_test"


async def _ctx_user_id_for(capture, db_client, *, triggered_by: str) -> str:
    """Run one turn as `triggered_by` and report the user_id ctx actually got.

    Also pins `_current_user_id`, the fourth consumer: it is assigned right
    after the replacement and read by the callback path. Asserted EQUAL to the
    captured value rather than to OWNER, so the unknown-agent case (which keeps
    the caller's argument) holds it to the same rule.
    """
    captured, runtime = await capture(db_client=db_client, agent_id=AGENT,
                                      user_id=triggered_by)
    assert runtime._current_user_id == captured["user_id"], (
        "the callback path's fallback and the turn's context disagree about "
        "who this run belongs to"
    )
    return captured["user_id"]


@pytest.fixture
async def seeded(db_client):
    await db_client.insert(
        "agents",
        {"agent_id": AGENT, "agent_name": "A", "created_by": OWNER},
    )
    return db_client


@pytest.fixture
async def seeded_without_owner(db_client):
    """An agent row whose `created_by` is empty. `NOT NULL` does not stop the
    empty string, and the guard treats it the same as a missing row."""
    await db_client.insert(
        "agents",
        {"agent_id": AGENT, "agent_name": "A", "created_by": ""},
    )
    return db_client


@pytest.mark.asyncio
async def test_a_peer_agents_message_still_runs_as_the_owner(
    capture_run_context, seeded
):
    """The bus trigger passes the SENDING AGENT as user_id."""
    got = await _ctx_user_id_for(
        capture_run_context, seeded, triggered_by="agent_peer_b"
    )
    assert got == OWNER


@pytest.mark.asyncio
async def test_two_different_speakers_land_on_the_same_user_id(
    capture_run_context, seeded
):
    """The one that matters for a team room: two teammates @mention the same
    agent, and both turns must key on the same value — otherwise instance,
    history, workspace and executor all fork per speaker."""
    first = await _ctx_user_id_for(
        capture_run_context, seeded, triggered_by="agent_ana"
    )
    second = await _ctx_user_id_for(
        capture_run_context, seeded, triggered_by="agent_bruno"
    )

    assert first == second == OWNER


@pytest.mark.asyncio
async def test_a_leader_patrol_runs_as_the_owner_too(capture_run_context, seeded):
    """The patrol passes a synthetic `team_<id>` marker rather than an agent —
    a third distinct value for the same room, and it must not fork either."""
    got = await _ctx_user_id_for(
        capture_run_context, seeded, triggered_by="team_t1"
    )
    assert got == OWNER


@pytest.mark.asyncio
async def test_an_unknown_agent_keeps_what_the_caller_passed(
    capture_run_context, db_client
):
    """No agent row, nothing to resolve — the argument stands rather than being
    replaced with an empty string. Pins the guard, not just the happy path: an
    override that fired on a missing row would blank out the workspace path.
    """
    got = await _ctx_user_id_for(
        capture_run_context, db_client, triggered_by="usr_x"
    )
    assert got == "usr_x"


@pytest.mark.asyncio
async def test_an_agent_without_an_owner_keeps_it_too(
    capture_run_context, seeded_without_owner
):
    """The guard's other half. Same outcome as a missing row, and the reason to
    pin it is the shape of the change that would break it: simplifying the
    condition to `if _agent:` reads harmless and blanks the workspace path.
    """
    got = await _ctx_user_id_for(
        capture_run_context, seeded_without_owner, triggered_by="usr_x"
    )
    assert got == "usr_x"
