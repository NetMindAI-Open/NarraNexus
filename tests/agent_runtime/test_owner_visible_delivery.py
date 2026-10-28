"""
@file_name: test_owner_visible_delivery.py
@date: 2026-08-04
@description: "Delivered to whoever contacted you" and "visible in the
owner's web chat" are two different predicates, and the session anchor
must only follow the second.

PR #230 review finding: expanding the bus handler's
``user_reply_tool_names`` (the delivered-to-origin list) silently flowed
into ``step_4._turn_delivered_user_message``, whose contract is
owner-chat visibility — every agent-to-agent bus reply would re-anchor
the OWNER's session onto the bus errand's narrative and clear
``last_query``. These tests pin the split: the handler exposes an
owner-visible subset (``owner_visible_reply_tool_names``, defaulting to
the full reply list for handlers whose channel IS the owner's surface),
and step_4 consumes only that.
"""
from __future__ import annotations

import narranexus.platform.message_bus  # noqa: F401 — registers the bus handler
from narranexus.platform.agent_runtime._agent_runtime_steps.step_4_persist_results import (
    _owner_visible_reply_texts,
    _turn_delivered_user_message,
)
from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
from narranexus.platform.schema import ProgressMessage
from narranexus.platform.schema.runtime_message import ProgressStatus


def _tool_progress(tool_name: str, content: str = "hello") -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={
            "tool_name": tool_name,
            "arguments": {"content": content},
        },
    )


# ---- handler-level contract ----------------------------------------------


def test_bus_handler_counts_bus_send_as_delivered_but_not_owner_visible():
    h = MessageSourceRegistry.get("message_bus")
    assert h.is_user_reply_tool("mcp__message_bus_module__message_team")
    assert not h.is_owner_visible_reply_tool(
        "mcp__message_bus_module__message_team"
    )
    assert h.is_owner_visible_reply_tool(
        "mcp__chat_module__notify_owner"
    )


def test_owner_visible_defaults_to_full_reply_list_for_im_handlers():
    """For IM channels the conversation IS with the owner — their channel
    tools stay owner-visible via the None-fallback."""
    h = MessageSourceRegistry.get("wechat")
    assert h.is_owner_visible_reply_tool("mcp__wechat_module__wechat_send")


def test_extract_owner_visible_text_gates_on_owner_list():
    h = MessageSourceRegistry.get("message_bus")
    assert (
        h.extract_owner_visible_text(
            "mcp__message_bus_module__message_agent", {"content": "peer reply"}
        )
        is None
    )
    assert (
        h.extract_owner_visible_text(
            "mcp__chat_module__notify_owner", {"content": "hi owner"}
        )
        == "hi owner"
    )


# ---- step_4 anchor predicate ---------------------------------------------


def test_bus_only_delivery_does_not_count_as_user_message():
    """A bus turn whose only delivery went to a peer agent must NOT flip
    the proactive-delivery branch — the owner saw nothing."""
    responses = [
        _tool_progress("mcp__message_bus_module__message_agent"),
        _tool_progress("mcp__message_bus_module__message_team"),
    ]
    assert _turn_delivered_user_message(responses, "message_bus") is False


def test_owner_relay_still_counts_as_user_message():
    responses = [_tool_progress("mcp__chat_module__notify_owner")]
    assert _turn_delivered_user_message(responses, "message_bus") is True


# ---- the shared traversal (2026-08-18) ------------------------------------
#
# `_turn_delivered_user_message` is now `bool(_owner_visible_reply_texts(...))`
# — one traversal, two readings. Before, the boolean predicate and the text
# extractor were separate copies of the same loop; a divergence between them
# would have shown up as the session anchor and the temporal guard disagreeing
# about whether the owner was messaged, and the guard's failure mode is to go
# quiet while its numbers still look healthy.
#
# The tests above pin the boolean reading. These pin the list reading, so the
# shared implementation is covered from both sides.


def test_owner_visible_texts_returns_every_reply_in_order():
    """Multi-reply turns are normal — the guard scans all of them, so none
    may be dropped and the order must hold."""
    responses = [
        _tool_progress("mcp__chat_module__reply_owner", "first"),
        _tool_progress("mcp__chat_module__reply_owner", "second"),
    ]
    assert _owner_visible_reply_texts(responses, "chat") == ["first", "second"]


def test_owner_visible_texts_excludes_peer_only_replies():
    """Same split the anchor relies on: a bus reply to a peer is a delivery,
    but no human read it, so it must not be measured as owner-facing text."""
    responses = [
        _tool_progress("mcp__message_bus_module__message_agent", "peer only"),
        _tool_progress("mcp__message_bus_module__notify_owner", "hi owner"),
    ]
    assert _owner_visible_reply_texts(responses, "message_bus") == ["hi owner"]


def test_owner_visible_texts_skips_blank_replies():
    """A reply that strips to blank is "nothing delivered" for the boolean
    reading; the list reading must agree, or the guard would scan "" and the
    anchor would still see a delivery."""
    responses = [_tool_progress("mcp__chat_module__reply_owner", "   ")]
    assert _owner_visible_reply_texts(responses, "chat") == []
    assert _turn_delivered_user_message(responses, "chat") is False


def test_owner_visible_texts_tolerates_malformed_responses():
    """Shape drift must degrade to "nothing delivered", never raise into
    step_4 — the wiring is diagnostic, the turn's real work is already done."""
    assert _owner_visible_reply_texts(None, "chat") == []
    assert _owner_visible_reply_texts(["not a ProgressMessage"], "chat") == []
    assert _owner_visible_reply_texts([], "unknown_source_xyz") == []


def test_boolean_predicate_agrees_with_the_list_on_every_case():
    """The equivalence the merge relies on, asserted directly rather than
    argued in a comment.

    The one input where the merged version does NOT match the pre-merge
    loop — a handler raising after a hit — needs a stubbed handler to
    reach, so it lives in its own test below rather than as a case here.
    """
    cases = [
        ([], "chat"),
        ([_tool_progress("mcp__chat_module__reply_owner")], "chat"),
        ([_tool_progress("mcp__message_bus_module__message_agent")], "message_bus"),
        ([_tool_progress("mcp__chat_module__reply_owner", "")], "chat"),
    ]
    for responses, source in cases:
        assert _turn_delivered_user_message(responses, source) == bool(
            _owner_visible_reply_texts(responses, source)
        ), (responses, source)


def test_non_progress_message_elements_are_filtered_not_fatal():
    """Junk in the response list is skipped by the isinstance guard — it does
    NOT reach the except, so a real reply beside it still counts."""
    responses = [
        _tool_progress("mcp__chat_module__reply_owner", "real reply"),
        object(),
    ]
    assert _owner_visible_reply_texts(responses, "chat") == ["real reply"]
    assert _turn_delivered_user_message(responses, "chat") is True


def test_a_raise_after_a_real_reply_reads_as_not_delivered(monkeypatch):
    """The one documented divergence from the pre-merge short-circuit.

    The old loop returned True at the first hit and never touched what came
    after. The merged version walks the whole response, so a handler that
    raises on a LATER element sends the traversal into its `except` and the
    predicate reports False.

    That is the intended choice: "the response could not be read cleanly"
    resolves to "not delivered", which is the conservative side for both
    consumers — the session anchor stays put, the guard skips a turn. Pinned
    here so the next person does not "fix" the except into `return texts` to
    salvage the partial list; that is what would let the anchor and the guard
    actually disagree.

    Reaching the except needs the handler itself to raise (`resp.details` is
    already isinstance-guarded), so the handler is stubbed rather than the
    input malformed — otherwise this test would silently assert nothing, the
    way a `ProgressMessage`-shaped fake would.
    """
    real = MessageSourceRegistry.get("chat")
    calls = {"n": 0}

    class _RaisesOnSecondCall:
        """Handlers are frozen dataclasses, so the stub replaces the whole
        handler via the registry rather than patching an attribute on one."""

        def extract_owner_visible_text(self, tool_name, arguments):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("handler blew up on a later element")
            return real.extract_owner_visible_text(tool_name, arguments)

    monkeypatch.setattr(
        MessageSourceRegistry, "get", staticmethod(lambda _src: _RaisesOnSecondCall())
    )

    responses = [
        _tool_progress("mcp__chat_module__reply_owner", "real reply"),
        _tool_progress("mcp__chat_module__reply_owner", "second"),
    ]
    assert _owner_visible_reply_texts(responses, "chat") == []
    assert _turn_delivered_user_message(responses, "chat") is False
