"""
@file_name: test_delivery_notice.py
@date: 2026-08-13
@description: The platform says so when a turn delivered nothing.

Three silences the room used to keep (PRD 2026-08-04, "看到的必须是真的"):

* a team turn that produced no reply text — the room stayed empty and the
  user read it as the agent ignoring them;
* the room post itself failing — backend green, billing charged, room empty;
* an A2A turn that neither answered the peer nor said anything to its owner —
  the asking agent waited forever.

All three now write a platform line. These tests pin the contract that makes
those lines safe to add: best-effort (a notice that cannot be posted must not
break the turn), platform-typed (so no consumer counts them as team activity),
and honest about the error without leaking a credential into the transcript.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from narranexus.platform.message_bus.delivery_notice import (
    DELIVERY_FAILED_MSG_TYPE,
    UNDELIVERED_MSG_TYPE,
    announce_delivery_failure,
    announce_undelivered,
)
from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    trigger_label,
)


def _bus() -> AsyncMock:
    bus = AsyncMock()
    bus.send_message = AsyncMock(return_value="msg_1")
    return bus


# ── the notice lands, typed and attributed ──────────────────────────────────


@pytest.mark.asyncio
async def test_undelivered_notice_is_posted_as_the_agent():
    """Attributed to the agent whose turn it was — the room's other lines are
    rendered by author, and an unattributed one would read as nobody's."""
    bus = _bus()
    assert await announce_undelivered(bus, "chan_1", "agent_b") is True

    kwargs = bus.send_message.await_args.kwargs
    assert kwargs["from_agent"] == "agent_b"
    assert kwargs["to_channel"] == "chan_1"
    assert kwargs["msg_type"] == UNDELIVERED_MSG_TYPE
    assert kwargs["mentions"] is None
    assert kwargs["content"]


@pytest.mark.asyncio
async def test_undelivered_notice_can_wake_the_agent_that_asked():
    """A2A: the peer who asked is the one left hanging, so it gets mentioned.

    The team-room call site passes no mentions — nobody is blocked there, and
    waking every member over a silence would be worse than the silence.
    """
    bus = _bus()
    await announce_undelivered(bus, "chan_1", "agent_b", mentions=["agent_a"])
    assert bus.send_message.await_args.kwargs["mentions"] == ["agent_a"]


@pytest.mark.asyncio
async def test_notices_inherit_the_trigger_tree():
    """Without root_run_id the lineage breaks here and a cascade stop would
    leave the branch beyond this notice running (same reason every other bus
    send carries it)."""
    bus = _bus()
    await announce_undelivered(bus, "chan_1", "agent_b", root_run_id="run_7")
    assert bus.send_message.await_args.kwargs["root_run_id"] == "run_7"

    bus = _bus()
    await announce_delivery_failure(
        bus, "chan_1", "agent_b", error="boom", root_run_id="run_7"
    )
    assert bus.send_message.await_args.kwargs["root_run_id"] == "run_7"


@pytest.mark.asyncio
async def test_delivery_failure_notice_carries_the_reason():
    bus = _bus()
    assert (
        await announce_delivery_failure(bus, "chan_1", "agent_b", error="DB is down")
        is True
    )
    kwargs = bus.send_message.await_args.kwargs
    assert kwargs["msg_type"] == DELIVERY_FAILED_MSG_TYPE
    assert "DB is down" in kwargs["content"]


@pytest.mark.asyncio
async def test_delivery_failure_notice_redacts_secrets():
    """Provider SDKs echo the offending key back in the error body, and this
    string lands in a transcript every team member can read."""
    bus = _bus()
    await announce_delivery_failure(
        bus, "chan_1", "agent_b",
        error="Incorrect API key provided: sk-abcdef0123456789abcdef",
    )
    content = bus.send_message.await_args.kwargs["content"]
    assert "sk-abcdef0123456789abcdef" not in content


# ── best-effort: a notice must never become the new failure ─────────────────


@pytest.mark.asyncio
async def test_a_notice_that_cannot_be_posted_reports_false_and_does_not_raise():
    """The delivery-failure notice travels the SAME path that just failed, so
    it failing too is the expected case, not an edge one. The caller needs a
    verdict (it falls back to the owner's inbox), never an exception."""
    bus = _bus()
    bus.send_message.side_effect = RuntimeError("still down")

    assert await announce_delivery_failure(bus, "chan_1", "agent_b", error="x") is False
    assert await announce_undelivered(bus, "chan_1", "agent_b") is False


# ── registered as a platform line, not as team activity ────────────────────


@pytest.mark.asyncio
async def test_both_types_are_registered_platform_messages():
    """Every consumer that counts/samples/summarises room activity excludes
    PLATFORM_MSG_TYPES. Forgetting to register a new type here is exactly the
    bug system_messages.py was created to stop: a self-trigger filter reopened
    from the side it did not know about."""
    assert UNDELIVERED_MSG_TYPE in PLATFORM_MSG_TYPES
    assert DELIVERY_FAILED_MSG_TYPE in PLATFORM_MSG_TYPES


def test_the_undelivered_notice_has_a_trigger_label():
    """It is the first platform type that carries mentions, so it is the first
    that can BECOME a trigger message. Without a label the prompt would print
    the raw sender to the woken agent."""
    label = trigger_label(UNDELIVERED_MSG_TYPE)
    assert label and label != trigger_label("some_unknown_type")
