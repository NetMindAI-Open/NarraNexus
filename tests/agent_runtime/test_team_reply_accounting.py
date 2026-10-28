"""
@file_name: test_team_reply_accounting.py
@author:
@date: 2026-08-12
@description: A team reply is a delivery, and the turn's books must say so.

## What this file used to be, and why most of it is gone (2026-08-17)

A team room used to be the one surface where the agent's PLAIN TEXT was the
reply and the platform posted it. The reply surface was emptied outright for
those turns, so no tool frame was reachable — the accounting was not merely
missing the reply, it was structurally incapable of seeing it, and every team
turn filed as "no reply sent" and started the next one cold. The fix at the time
moved delivery into the turn as a callback, and most of this file drove that
callback, its gate, and their wiring.

**The callback is gone.** A team reply is a `message_team` tool call now, like
every other surface's reply, so the accounting sees it the ordinary way and
there is no gate to test: nothing decides whether the platform may speak on the
agent's behalf, because it never does. Fourteen tests were removed for that
reason and no other — their subject stopped existing. Each was checked
individually against "what does this guard, and does that thing still exist?"
before it went.

## What still holds, and is still pinned here

  * **`message_team` / `message_agent` count as delivered but NOT as
    owner-visible.** This survived the redesign unchanged and is the
    load-bearing one: without it, every agent-to-agent reply re-anchors the
    owner's chat session — a bug this repo has already fixed once, and one the
    cold-start work depends on staying fixed.
  * **What counts as a fatal turn** (`RunCollection.is_fatal` and the
    collector's sticky-fatal rule). Unrelated to team delivery; it gates the
    helper-LLM fallback and is consulted every turn.
"""
from __future__ import annotations

import pytest


PLATFORM_KEY = "_platform_reply_text"


def _frames(agent_loop_response):
    """Tool-ish frames the accounting layer would inspect."""
    out = []
    for r in agent_loop_response:
        details = getattr(r, "details", None) or {}
        if details.get("tool_name"):
            out.append(details)
    return out


def test_the_team_frame_is_not_owner_visible():
    """Load-bearing invariant, and the reason it gets its own test.

    `message_team` counts as "delivered to whoever contacted you" but NOT as
    "the owner saw it" — the split exists because otherwise every team reply
    re-anchors the owner's chat session. A later change that promotes it to the
    owner-visible list would silently reintroduce the bug, and the cold-start
    work planned on top of this depends on it holding.

    Now a DIRECT test of a real team reply rather than of a synthetic frame
    standing in for one: the agent calls `message_team` itself, so the name
    asserted below is the name that actually appears in the turn's trace.
    """
    import narranexus.platform.message_bus  # noqa: F401 — registers the handler
    from narranexus.platform.channel.message_source_handler import (
        MessageSourceRegistry,
    )

    handler = MessageSourceRegistry.get("message_bus")
    # The full tool name the agent actually calls — asserting the bare stem
    # would not even prove the prefix matching works.
    tool = "mcp__message_bus_module__message_team"

    # Through the real consumers, NOT the raw fields. The natural way to break
    # this invariant is to delete the `owner_visible_reply_tool_names=` line, at
    # which point the field is None and `effective_owner_visible_names` falls
    # back to the full reply list — reviving the bug. An earlier version of this
    # test asserted `"<the tool> not in (field or [])"`, which stays green
    # through exactly that change: `None or []` is empty, so the assertion is
    # vacuous precisely when it matters.
    assert handler.is_user_reply_tool(tool) is True
    assert handler.is_owner_visible_reply_tool(tool) is False

    # The peer lane carries the identical risk and is easy to forget: a DM to
    # another agent is just as invisible to the owner, and `message_agent` was
    # added to the handler in the same change. Asserting only the team name
    # would leave the newer of the two names unguarded.
    peer = "mcp__message_bus_module__message_agent"
    assert handler.is_user_reply_tool(peer) is True
    assert handler.is_owner_visible_reply_tool(peer) is False

    # And the tool that IS owner-visible on a bus turn, so the test proves the
    # handler distinguishes rather than just answering False to everything.
    owner = "mcp__chat_module__notify_owner"
    assert handler.is_owner_visible_reply_tool(owner) is True


def test_a_returned_fatal_counts_even_though_nothing_was_raised():
    """`auth_expired` and `config_actionable` are marked fatal and RETURNED as
    frames, so the loop never throws. A gate reading only "did it raise" lets
    them through — and for a team room that means posting whatever streamed
    before the failure, unmarked, in front of everyone."""
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )
    from narranexus.platform.schema import ErrorMessage

    frame = ErrorMessage(
        error_message="provider key expired",
        error_type="auth_expired",
        severity="fatal",
    )

    assert _turn_hit_a_fatal(None, [frame]) is True


def test_a_recoverable_hiccup_is_not_a_failed_turn():
    """The loop absorbed it and went on to answer. Calling this a failure both
    discards the real reply and announces a breakdown that did not happen."""
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )
    from narranexus.platform.schema import ErrorMessage

    frame = ErrorMessage(
        error_message="429 from provider, retried",
        error_type="rate_limit",
        severity="recoverable",
    )

    assert _turn_hit_a_fatal(None, [frame]) is False


def test_a_raised_failure_still_counts():
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (  # noqa: E501
        _turn_hit_a_fatal,
    )

    assert _turn_hit_a_fatal({"error_type": "TimeoutError"}, []) is True


def test_a_recoverable_error_does_not_make_the_run_look_failed():
    """The collector's side of the same fact.

    `is_error` is set by any error frame; `is_fatal` is what a consumer deciding
    "did this turn produce usable output" has to read. Conflating them is what
    put a failure notice next to a correct answer.
    """
    from narranexus.platform.agent_runtime.run_collector import RunCollection, RunError

    recoverable = RunCollection(
        output_text="here is your answer",
        tool_calls=[], raw_items=[],
        error=RunError("rate_limit", "429, retried", severity="recoverable"),
    )

    assert recoverable.is_error is True
    assert recoverable.is_fatal is False


def test_an_unlabelled_error_is_treated_as_fatal():
    """The safe direction: presenting a possibly-empty turn as a success is the
    more harmful mistake."""
    from narranexus.platform.agent_runtime.run_collector import RunCollection, RunError

    unlabelled = RunCollection(
        output_text="", tool_calls=[], raw_items=[],
        error=RunError("SomeCrash", "boom"),
    )

    assert unlabelled.is_fatal is True


def test_a_recovered_turn_is_not_fatal():
    """`recovered` means a fatal-class failure was papered over by the
    helper-LLM fallback, which produced a real reply. Calling it fatal throws
    that reply away and puts a failure notice in its place — the user is
    entitled to the answer that was produced for them."""
    from narranexus.platform.agent_runtime.run_collector import RunCollection, RunError

    c = RunCollection(
        output_text="the fallback answer", tool_calls=[], raw_items=[],
        error=RunError("api_error", "sdk crashed", severity="recovered"),
    )

    assert c.is_error is True
    assert c.is_fatal is False


def test_a_turn_that_already_spoke_is_not_fatal():
    """`recovered_after_reply`: the agent replied, THEN something broke. The
    reply happened; the badge is for the unfinished remainder."""
    from narranexus.platform.agent_runtime.run_collector import RunCollection, RunError

    c = RunCollection(
        output_text="here you go", tool_calls=[], raw_items=[],
        error=RunError("api_error", "died after replying",
                       severity="recovered_after_reply"),
    )

    assert c.is_fatal is False


def test_only_fatal_and_unlabelled_count_as_fatal():
    from narranexus.platform.agent_runtime.run_collector import RunCollection, RunError

    for sev, expected in [
        ("fatal", True), ("", True),
        ("recoverable", False), ("recovered", False),
        ("recovered_after_reply", False),
    ]:
        c = RunCollection(
            output_text="", tool_calls=[], raw_items=[],
            error=RunError("e", "m", severity=sev),
        )
        assert c.is_fatal is expected, f"severity={sev!r}"


# ── severity survives the collector, not just the dataclass ─────────────────
#
# The three tests above build a `RunCollection` by hand, which is exactly why
# they could not see that `collect_run`'s own sticky-fatal step was rewriting
# `recovered` back to `"fatal"` on the way out — undoing the distinction they
# assert. These drive the collector.

async def _collect(frames):
    from narranexus.platform.agent_runtime.run_collector import collect_run

    class _Runtime:
        def run(self, **_kw):
            async def _gen():
                for f in frames:
                    yield f
            return _gen()

    return await collect_run(
        _Runtime(), agent_id="a", user_id="u", input_content="hi",
        working_source="message_bus",
    )


def _err(severity: str):
    from narranexus.platform.schema import ErrorMessage

    return ErrorMessage(
        error_message="boom", error_type="api_error", severity=severity,
    )


@pytest.mark.asyncio
async def test_a_recovered_run_survives_the_collector_as_non_fatal():
    """`recovered` only ever follows a fatal — that is its precondition, not a
    contradiction. The sticky rule exists for a LESS informed frame arriving
    late; this one is more informed, and overruling it discards the reply the
    fallback produced."""
    c = await _collect([_err("fatal"), _err("recovered")])

    assert c.is_error is True
    assert c.is_fatal is False


@pytest.mark.asyncio
async def test_a_turn_that_spoke_before_dying_survives_the_collector():
    c = await _collect([_err("fatal"), _err("recovered_after_reply")])

    assert c.is_fatal is False


@pytest.mark.asyncio
async def test_a_recoverable_frame_after_a_fatal_does_not_rescue_the_run():
    """The case the sticky rule is FOR: the late frame knows less, so the
    fatal stands."""
    c = await _collect([_err("fatal"), _err("recoverable")])

    assert c.is_fatal is True
