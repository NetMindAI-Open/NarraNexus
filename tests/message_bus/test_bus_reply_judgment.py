"""
@file_name: test_bus_reply_judgment.py
@date: 2026-08-04
@description: "Did this turn deliver to whoever contacted the agent?" —
pinned at its REAL consumer, not just the handler data.

2026-08-01 briefing-squad incident: agents that DID deliver via
``message_team`` were recorded NO-REPLY. The handler's
``user_reply_tool_names`` now carries the bus delivery tools, and the
live consumer is ChatModule's background-persistence split: a bus turn
that delivered to its origin writes a [DELIVERED-BG] activity row (and
an honest summary), only a genuinely silent turn writes [NO-REPLY-BG].
That split is the no-reply metric the fallback decision reads — round-2
review caught that after the owner-visible separation the expanded list
briefly had NO live consumer and the metric stayed poisoned.
"""
from __future__ import annotations

import narranexus.platform.message_bus  # noqa: F401 — triggers registration
from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
from narranexus_plugins.chat_module.chat_module import ChatModule
from narranexus.platform.schema import ProgressMessage
from narranexus.platform.schema.runtime_message import ProgressStatus


def _handler():
    return MessageSourceRegistry.get("message_bus")


def _tool_progress(tool_name: str, content: str = "hello") -> ProgressMessage:
    return ProgressMessage(
        step="3.4.1",
        title="Tool call",
        description=tool_name,
        status=ProgressStatus.COMPLETED,
        details={"tool_name": tool_name, "arguments": {"content": content}},
    )


# ---- the data: handler reply list -----------------------------------------


def test_bus_sends_count_as_delivery_tools():
    assert _handler().is_user_reply_tool("mcp__message_bus_module__message_team")
    assert _handler().is_user_reply_tool("mcp__message_bus_module__message_agent")
    assert _handler().is_user_reply_tool(
        "mcp__chat_module__notify_owner"
    )


def test_non_delivery_bus_tools_do_not_count():
    """Every REAL non-send tool, enumerated from the registration.

    This used to name `bus_get_messages`, a tool retired in the same change that
    renamed the send verbs — so it asserted nothing about the code that exists.
    Matching is by SUBSTRING (`pattern in tool_name`), so what actually needs
    guarding is that no real tool name CONTAINS `message_agent`, `message_team`
    or `notify_owner`. Rename a send verb to something a `find_agent`-style tool
    contains and every silent turn files as delivered, poisoning the no-reply
    metric the delivery-fallback decision reads.

    Enumerated rather than listed, so a tool added later is covered without
    anyone remembering to add it here.
    """
    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools

    SENDS = {"message_agent", "message_team"}
    names: list[str] = []

    class _Stub:
        def tool(self, *_a, **_k):
            def _wrap(fn):
                names.append(fn.__name__)
                return fn
            return _wrap

    async def _no_bus():
        return None

    _message_bus_mcp_tools.register_message_bus_mcp_tools(_Stub(), _no_bus)
    assert len(names) >= 5, f"only {len(names)} tools collected: {names}"

    handler = _handler()
    for name in names:
        qualified = f"mcp__message_bus_module__{name}"
        if name in SENDS:
            assert handler.is_user_reply_tool(qualified), name
        else:
            assert not handler.is_user_reply_tool(qualified), (
                f"{name} is not a send verb but counts as a reply — a silent "
                f"turn calling it would file as delivered"
            )


# ---- the live consumer: delivered-to-origin split -------------------------


def test_bus_delivery_is_recognized_at_the_persistence_split():
    delivered = bool(ChatModule._origin_delivered_text(
        "message_bus",
        [_tool_progress("mcp__message_bus_module__message_team", "peer reply")],
    ))
    assert delivered is True


def test_silent_bus_turn_is_not_delivered():
    """A turn whose only tool call was a READ delivered nothing.

    `bus_get_messages` — the name this used to pass — no longer exists, so the
    assertion was about a string rather than about the code. `read_history` is the
    live read tool and the one an agent actually calls before answering.
    """
    delivered = bool(ChatModule._origin_delivered_text(
        "message_bus",
        [_tool_progress("mcp__message_bus_module__read_history", "")],
    ))
    assert delivered is False


def test_the_activity_summary_only_describes_silence_now():
    """This summary is reached only by turns that said nothing to anyone.

    It used to carry a "Replied to X" arm as well, chosen by a
    `delivered_to_origin` flag. That arm became unreachable once a delivered
    turn started recovering its text and being written as a real assistant row
    — and a branch that cannot run is a claim about the code that is no longer
    true. What has to hold now is that the silent wording never overstates.
    """
    meta = {"channel_tag": {"sender_name": "Maestro"}}

    summary = ChatModule._build_activity_summary("message_bus", meta)

    assert "Replied" not in summary  # the old unconditional "Replied to X" lied
    assert "no reply" in summary.lower()
    assert "Maestro" in summary  # who it read, when the tag says
