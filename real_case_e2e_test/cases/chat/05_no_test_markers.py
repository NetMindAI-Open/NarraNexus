"""
@file_name: 05_no_test_markers.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Verify no debug / test markers leak into user-visible reply

Reproduces the spirit of Lark bug #3 ("Agent sent test messages to
Lark"). We send a benign user query and assert the user-visible reply
does not contain any obvious leftover-debug strings. Cheap, single
turn, exercises every release.
"""

from real_case_e2e_test.core.case_spec import CaseSpec, TalkLine


SPEC = CaseSpec(
    case_id="chat/05_no_test_markers",
    pillar="chat",
    description="Agent reply must not leak debug/test markers from internal code paths.",
    linked_bugs=["#3"],
    severity="P0",
    tags=["needs-llm", "single-turn", "debug-leak"],
    turn_timeout_seconds=60,
    semantic_intent=(
        "The reply should be plain, on-topic content. Strings like "
        "'test message', '测试信息', 'TODO', 'stub_reply', or any obvious "
        "developer marker indicate a code path that should never have "
        "been reachable in production."
    ),
)


TALK: list[TalkLine] = [
    TalkLine(
        role="user",
        content="今天上海的天气大概怎么样？随便回答即可。",
        expect_not_contains=[
            "(Agent decided no response needed)",
            "test message",
            "测试信息",
            "stub_reply",
        ],
    ),
]


async def run(ctx):
    user = await ctx.fixtures.make_user()
    agent = await ctx.fixtures.make_agent(user, name="e2e_no_markers_agent")
    for line in TALK:
        await ctx.drive_turn(user=user, agent=agent, line=line)
