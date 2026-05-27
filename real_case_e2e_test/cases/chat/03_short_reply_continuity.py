"""
@file_name: 03_short_reply_continuity.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Short follow-up reply ("好") after an options prompt — does
              the agent stay on the same narrative, or fall back to a
              default / unrelated topic?

Reproduces the symptom recorded for Lark bug #7: the agent presents
options A/B/C, the user replies with a one-character affirmation
("好"), narrative continuity_detect mis-matches, and the next reply is
off-topic.
"""

from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="chat/03_short_reply_continuity",
    pillar="chat",
    description="Single-character affirmation after a proposal must keep narrative continuity.",
    linked_bugs=["#7"],
    severity="P0",
    tags=["needs-llm", "multi-turn", "narrative", "short-reply"],
    turn_timeout_seconds=120,
    semantic_intent=(
        "Turn two reply must continue the topic from turn one (a todo app "
        "technology stack discussion). A drift to unrelated content, a "
        "request to clarify what '好' refers to, or a fallback to a "
        "default narrative are semantic failures even if programmatic "
        "gates pass."
    ),
)


TALK: list[TalkLine] = [
    TalkLine(
        role="user",
        content="我想做一个个人 todo app。你有 3 个技术栈方案吗？给我 A / B / C 三个简短选项即可。",
        expect_not_contains=["(Agent decided no response needed)"],
    ),
    TalkLine(
        role="user",
        content="好",
        expect_not_contains=["(Agent decided no response needed)"],
    ),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user, name="e2e_continuity_agent")
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, line=line)
