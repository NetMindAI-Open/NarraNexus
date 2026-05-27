"""
@file_name: test_message_read_handler.py
@author: Bin Liang
@date: 2026-05-19
@description: Lark EventDispatcher must register a processor for every
IM event the SDK can deliver, otherwise the SDK floods ERROR logs with
``processor not found`` — a 90-minute EC2 sample on 2026-05-18 showed
48 such errors purely from message-read receipts.

Original scope (2026-05-19, commit fbd0c69) added a no-op for
``im.message.message_read_v1``. This file was broadened on 2026-05-22
after we noticed the same SDK design (strict whitelist dispatch — no
default / catch-all processor) means any *other* unregistered IM event
type would log the same way. Real-world triggers include message
recall, emoji reactions, bot/user join-leave in groups — none of which
we currently act on, but all of which silently noise the log.

The fix is defensive: every IM event the lark_oapi SDK exposes is
registered, either with the real handler (``message_receive_v1``) or
with a no-op. This test pins down the full set so a future SDK upgrade
that surfaces NEW IM events would force us to revisit (the test would
not catch missing newly-added events automatically — they don't exist
yet — but the production code now passes through one explicit helper
so the diff is centralised).
"""
from __future__ import annotations

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


# Every p2 IM event the lark_oapi SDK exposes a register_p2_im_* method
# for, expressed as the canonical event_key under
# ``EventDispatcherHandler._processorMap``. Update this list when the
# SDK adds new IM events (run
# ``grep "register_p2_im" .venv/.../dispatcher_handler.py`` to enumerate).
_EXPECTED_IM_EVENT_KEYS: frozenset[str] = frozenset(
    {
        "p2.im.message.receive_v1",
        "p2.im.message.message_read_v1",
        "p2.im.message.recalled_v1",
        "p2.im.message.reaction.created_v1",
        "p2.im.message.reaction.deleted_v1",
        "p2.im.chat.disbanded_v1",
        "p2.im.chat.updated_v1",
        "p2.im.chat.access_event.bot_p2p_chat_entered_v1",
        "p2.im.chat.member.bot.added_v1",
        "p2.im.chat.member.bot.deleted_v1",
        "p2.im.chat.member.user.added_v1",
        "p2.im.chat.member.user.deleted_v1",
        "p2.im.chat.member.user.withdrawn_v1",
    }
)


def test_event_handler_registers_message_receive_and_read():
    """Original assertion — kept verbatim so a regression here is
    obvious in CI output."""
    def noop_recv(data):  # pragma: no cover — handler body irrelevant
        pass

    def noop_read(data):  # pragma: no cover
        pass

    handler = LarkTrigger._build_event_handler(noop_recv, noop_read)

    keys = set(handler._processorMap.keys())  # noqa: SLF001 — SDK internal
    assert "p2.im.message.receive_v1" in keys, (
        f"missing receive_v1 processor; got {keys}"
    )
    assert "p2.im.message.message_read_v1" in keys, (
        f"missing message_read_v1 processor; got {keys}"
    )


def test_event_handler_registers_every_known_im_event():
    """Defensive ledger: every IM event the SDK can dispatch must have
    SOME processor (real or no-op). Without this every reaction,
    recall, or member-change push from Lark spams a "processor not
    found" ERROR."""
    def noop_recv(data):  # pragma: no cover
        pass

    def noop_read(data):  # pragma: no cover
        pass

    handler = LarkTrigger._build_event_handler(noop_recv, noop_read)
    registered = set(handler._processorMap.keys())  # noqa: SLF001

    missing = _EXPECTED_IM_EVENT_KEYS - registered
    assert not missing, (
        "Lark EventDispatcher missing processors for IM events: "
        f"{sorted(missing)}. Without these the SDK logs an ERROR per "
        "received event."
    )
