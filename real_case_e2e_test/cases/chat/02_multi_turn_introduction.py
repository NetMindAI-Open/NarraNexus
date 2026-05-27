"""
@file_name: 02_multi_turn_introduction.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Two-turn dialogue; second turn must show context retention

The case asks the agent to introduce itself, then on turn two asks a
follow-up that only makes sense if the agent remembered turn one.
Programmatic gate: both turns complete, both produce user-visible
messages, no fatal errors. Semantic gate: turn-two reply actually
references the context established in turn one.
"""

from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="chat/02_multi_turn_introduction",
    pillar="chat",
    description="Two-turn intro + context recall; second turn must reference turn-one state.",
    linked_bugs=[],
    severity="P1",
    tags=["needs-llm", "multi-turn", "context-retention"],
    turn_timeout_seconds=120,
    semantic_intent=(
        "On turn two the agent must demonstrate it remembered the user's "
        "first message — at minimum it should reference what was just "
        "introduced rather than starting a brand-new topic. A coherent "
        "follow-up is the pass condition; restarting the conversation "
        "is a semantic failure."
    ),
)


TALK: list[TalkLine] = [
    TalkLine(
        role="user",
        content="你好，我叫小航。请用一句话回应一下。",
        expect_not_contains=["(Agent decided no response needed)"],
    ),
    TalkLine(
        role="user",
        content="刚刚我告诉你我叫什么了吗？请直接重复我的名字。",
        expect_not_contains=["(Agent decided no response needed)"],
        expect_contains=["小航"],
    ),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user, name="e2e_intro_agent")
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, line=line)
