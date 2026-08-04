"""
@file_name: test_bus_send_stamp.py
@author: NarraNexus
@date: 2026-08-03
@description: A bus send stamps ITSELF, per target — not per turn.

Found in PR #229 review: stamping the whole turn with
``BUS_ERRAND_TURN_SOURCE`` moved the P1 one seat over instead of fixing it.
A bus turn is not homogeneous — ``MessageBusModule.hook_data_gathering``
injects unread bus messages from ALL channels every turn (``bus.get_unread``
JOINs across channel membership) and the module prompt REQUIRES answering
them ("A question is never ping-pong — answer it"). So an errand-continuation
turn routinely also answers an unrelated peer C:

    1. C asks A on its own owner's behalf (chat turn)        → A answers the peer ✅
    2. B answers A's errand (message_bus turn)               → A relays to owner ✅
    3. A answers C *inside that same turn*                   → ???

With a whole-turn stamp step 3 was written as "message_bus_errand", C read it
as a QUESTION, and C — which had promised its own owner a report — stopped
relaying. That is the exact failure this PR exists to fix.

So ``_send_turn_source`` compares each send's target against the turn's errand
scope: only the errand peer/channel gets the errand stamp.
"""
from __future__ import annotations

import contextlib

import pytest

from xyz_agent_context.module._mcp_identity import agent_id_headers
from xyz_agent_context.module.message_bus_module._message_bus_mcp_tools import (
    _send_turn_source,
)
from xyz_agent_context.schema import BUS_ERRAND_TURN_SOURCE

ME = "agent_xiaoque"
ERRAND_PEER = "agent_yushu"       # answered our errand → follow-ups go here
OTHER_PEER = "agent_c"            # asked us something, arrived via unread
ERRAND_CHANNEL = "ch_dm_errand"
OTHER_CHANNEL = "ch_dm_other"


class _Headers(dict):
    def get(self, key, default=None):  # noqa: D102
        return super().get(key.lower(), default)


@contextlib.contextmanager
def injected(headers: dict):
    from mcp.server.lowlevel.server import request_ctx

    request = type("Req", (), {
        "headers": _Headers({k.lower(): v for k, v in headers.items()})
    })()
    token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        request_ctx.reset(token)


def _errand_turn(**kwargs) -> dict:
    """Headers for a bus turn that is continuing our own errand."""
    return agent_id_headers(
        ME, turn_source="message_bus",
        errand_peer=ERRAND_PEER, errand_channel=ERRAND_CHANNEL,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The regression this file exists for
# ---------------------------------------------------------------------------


def test_answering_an_unrelated_peer_in_an_errand_turn_stays_plain():
    """Step 3 above. C must keep reading our message as an ANSWER, so C still
    relays to its own owner."""
    with injected(_errand_turn()):
        assert _send_turn_source(to_agent=OTHER_PEER) == "message_bus"
        assert _send_turn_source(channel_id=OTHER_CHANNEL) == "message_bus"


def test_following_up_with_the_errand_peer_is_stamped_as_an_errand():
    """Path A: the Owner Relay directive itself tells us to ask clarifying
    questions with bus_send_to_agent. That send is a QUESTION."""
    with injected(_errand_turn()):
        assert _send_turn_source(to_agent=ERRAND_PEER) == BUS_ERRAND_TURN_SOURCE
        assert _send_turn_source(channel_id=ERRAND_CHANNEL) == BUS_ERRAND_TURN_SOURCE


def test_codex_shape_reaches_the_same_verdict():
    """Codex forwards nothing but the bearer, so the scope must survive there
    too — a header-only fact was the previous round's hole."""
    bearer_only = {"Authorization": _errand_turn()["Authorization"]}
    with injected(bearer_only):
        assert _send_turn_source(to_agent=ERRAND_PEER) == BUS_ERRAND_TURN_SOURCE
        assert _send_turn_source(to_agent=OTHER_PEER) == "message_bus"


# ---------------------------------------------------------------------------
# Everything else keeps recording the plain turn kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["chat", "job", "lark"])
def test_owner_facing_turns_are_never_upgraded(source):
    """An owner-facing turn is already an unambiguous question; upgrading it
    would tell the recipient to answer a peer instead of its owner."""
    headers = agent_id_headers(
        ME, turn_source=source, errand_peer=ERRAND_PEER,
        errand_channel=ERRAND_CHANNEL,
    )
    with injected(headers):
        assert _send_turn_source(to_agent=ERRAND_PEER) == source


def test_bus_turn_without_an_errand_scope_stays_plain():
    """A turn spent purely answering a peer has no errand of its own."""
    with injected(agent_id_headers(ME, turn_source="message_bus")):
        assert _send_turn_source(to_agent=ERRAND_PEER) == "message_bus"
        assert _send_turn_source(channel_id=ERRAND_CHANNEL) == "message_bus"


def test_no_headers_records_nothing_rather_than_guessing():
    """No injection (a direct MCP client, an adapter we have not taught): the
    recipient degrades on its own; we must not invent a stamp."""
    assert _send_turn_source(to_agent=ERRAND_PEER) is None


def test_an_empty_target_never_matches_an_empty_scope():
    """Both sides of the comparison can be empty — that must read as "no
    match", not as "this is the errand"."""
    with injected(agent_id_headers(ME, turn_source="message_bus")):
        assert _send_turn_source() == "message_bus"
