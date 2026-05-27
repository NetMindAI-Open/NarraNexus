"""
@file_name: 04_concise_constraint.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Hard length constraint — does the agent honor an explicit
              upper bound on reply length?

Single turn, single message. The agent is told "answer in 20 Chinese
characters or fewer" and asked a yes/no question. The programmatic
gate checks the user-visible reply against the constraint; the
semantic gate confirms the reply is actually a direct answer and not
a hedge.
"""

from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="chat/04_concise_constraint",
    pillar="chat",
    description="Agent must answer within an explicit 20-char limit.",
    linked_bugs=[],
    severity="P2",
    tags=["needs-llm", "single-turn", "instruction-following"],
    turn_timeout_seconds=60,
    semantic_intent=(
        "The agent should give a direct yes/no answer in 20 Chinese "
        "characters or fewer. Hedging, long disclaimers, or verbose "
        "qualifiers are semantic failures even when programmatic gates "
        "pass."
    ),
)


TALK: list[TalkLine] = [
    TalkLine(
        role="user",
        content="请用不超过 20 个汉字回答：北京是中国的首都吗？",
        expect_not_contains=["(Agent decided no response needed)"],
        expect_contains=["是"],
    ),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user, name="e2e_concise_agent")
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, line=line)
