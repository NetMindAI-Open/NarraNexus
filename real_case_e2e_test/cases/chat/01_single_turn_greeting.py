"""
@file_name: 01_single_turn_greeting.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Demo case — one-turn greeting

This is the canonical "minimum viable case" the README points at. It
exists to:

  - show what the case-author surface actually looks like (SPEC, TALK,
    six-line ``run``)
  - exercise the absolute baseline of the agent (one user, one agent,
    one turn) so that any breakage of the chat path is the first thing
    a regression run catches

It is **not** trying to test a specific bug — for that we'd encode the
chain explicitly in TALK with expect_contains, and link the bug id in
``linked_bugs``. Treat this case as the smoke that the e2e harness
itself works.
"""

from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="chat/01_single_turn_greeting",
    pillar="chat",
    description="One-turn greeting; agent must reply non-empty within the turn timeout.",
    linked_bugs=[],
    severity="P0",
    tags=["needs-llm", "single-turn", "baseline"],
    turn_timeout_seconds=120,
    semantic_intent=(
        "The agent should produce a brief, on-topic self-introduction in "
        "Chinese. Off-topic, empty, or English-only replies are semantic "
        "failures even when the programmatic gate is green."
    ),
)


TALK: list[TalkLine] = [
    TalkLine(
        role="user",
        content="你好，请用一句话简单介绍一下你自己。",
        expect_not_contains=["(Agent decided no response needed)"],
    ),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user, name="e2e_greeting_agent")
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, line=line)
