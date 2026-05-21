"""
@file_name: test_message_read_handler.py
@author: Bin Liang
@date: 2026-05-19
@description: Lark EventDispatcher must register a no-op processor for
`im.message.message_read_v1` so the SDK does not flood ERROR logs with
"processor not found" for every read receipt.

Observed in EC2 lark container 2026-05-18T17:36:57 → 19:06:39 (48 hits
in 90 minutes — one per Lark message-read event for any of our bots).

The fix extracts the previously inline event-dispatcher build into a
static helper so it is testable in isolation; the production
`_subscribe_loop` now delegates to that helper.
"""
from __future__ import annotations

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


def test_event_handler_registers_message_receive_and_read():
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
