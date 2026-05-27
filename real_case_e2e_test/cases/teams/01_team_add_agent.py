"""
@file_name: 01_team_add_agent.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Reproduce Lark bug #13 — local team add-agent flow

Pure REST, no LLM. The case creates one local user, gives them one
agent and one team, then asks the team API to add that agent as a
member. All operations route via the same query-param ``user_id``,
so every endpoint resolves the same owner — when teams.py honors the
query param the way the rest of local mode does, this case is green.
"""

from real_case_e2e_test.core.api_client import APIError, APILogicError
from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="teams/01_team_add_agent",
    pillar="teams",
    description="user → agent → team → add_member chain must complete with success.",
    linked_bugs=["#13"],
    severity="P0",
    tags=["no-llm", "rest", "isolation"],
    turn_timeout_seconds=30,
    semantic_intent=(
        "This is a pure REST case; the semantic phase has little to "
        "judge beyond confirming the manifest reasons are coherent."
    ),
)


# No TalkLine driving needed (no LLM). The schema requires the list
# to exist; the runner is comfortable with an empty TALK as long as
# `run` produces a transcript.
TALK: list[TalkLine] = []


async def run(ctx):
    user = await ctx.fixtures.make_user()
    # No LLM provider needed — this case never talks to an agent over WS.
    agent = await ctx.fixtures.make_agent(user, with_llm=False, name="e2e_team_member_agent")

    # Drop a fact into the transcript so the report can show what
    # actually happened beyond turn data.
    try:
        team = await ctx.api._post(
            "/api/teams",
            {"name": f"{ctx.spec.case_id}_team", "description": "smoke teams case"},
            params={"user_id": user.user_id},
        )
    except (APIError, APILogicError) as exc:
        ctx.transcript.driver_error = f"create-team failed: {exc}"
        return

    if not team.get("success") or "team" not in team:
        ctx.transcript.driver_error = f"create-team unexpected body: {team!r}"
        return
    team_id = team["team"]["team_id"]
    team_owner_returned = team["team"].get("owner_user_id")
    ctx.transcript.agent_ids.append(team_id)  # convenient bag for the report

    try:
        add = await ctx.api._post(
            f"/api/teams/{team_id}/members",
            {"agent_id": agent.agent_id},
            params={"user_id": user.user_id},
        )
    except APIError as exc:
        ctx.transcript.driver_error = (
            f"add-member rejected (Bug #13 repro): {exc}. "
            f"team owner returned by backend = {team_owner_returned!r}, "
            f"agent created_by = {user.user_id!r}"
        )
        return

    if not add.get("success"):
        ctx.transcript.driver_error = (
            f"add-member success=False (Bug #13 repro): {add!r}. "
            f"team owner returned = {team_owner_returned!r}, "
            f"agent created_by = {user.user_id!r}"
        )
        return
