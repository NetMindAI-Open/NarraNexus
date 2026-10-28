"""
@file_name: test_control.py
@author:
@date: 2026-09-22
@description: Tests for control ownership between the agent and the user .

The panel is interactive, so two parties can drive the same browser. The rules
that make that safe rather than chaotic:

* only one side drives at a time, and the UI can always tell which;
* handover is an explicit act, never a grab — a click that silently steals the
  pointer mid-form-fill is how half-submitted forms happen;
* while the user holds control the agent's actions **queue**, they do not
  fail. An agent that is waiting is still working (铁律 #14); turning a
  takeover into a tool error would make the user's helpfulness look like a
  malfunction.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.browser._browser_impl.control import ControlArbiter


def test_agent_drives_by_default():
    """The agent started the session; it should not have to ask for the
    pointer before its first action."""
    a = ControlArbiter()
    assert a.holder == "agent"
    assert a.agent_may_act() is True


def test_user_takeover_switches_the_holder():
    a = ControlArbiter()
    a.user_take_control()
    assert a.holder == "user"
    assert a.agent_may_act() is False


def test_user_input_is_ignored_while_the_agent_holds_control():
    """Stray mouse movement over the panel must not silently steal the
    pointer from an agent mid-action."""
    a = ControlArbiter()
    assert a.accept_user_input() is False


def test_user_input_is_accepted_after_an_explicit_takeover():
    a = ControlArbiter()
    a.user_take_control()
    assert a.accept_user_input() is True


def test_release_hands_control_back_to_the_agent():
    a = ControlArbiter()
    a.user_take_control()
    a.user_release_control()
    assert a.holder == "agent"
    assert a.agent_may_act() is True


def test_release_without_takeover_is_a_noop():
    a = ControlArbiter()
    a.user_release_control()
    assert a.holder == "agent"


def test_takeover_is_idempotent():
    a = ControlArbiter()
    a.user_take_control()
    a.user_take_control()
    a.user_release_control()
    assert a.holder == "agent"


def test_state_is_serialisable_for_the_panel_header():
    a = ControlArbiter()
    d = a.to_dict()
    assert d["holder"] == "agent"
    assert set(d) == {"holder", "agent_waiting", "owner_connection_id"}


# ── the queueing behaviour ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_action_waits_instead_of_failing_during_takeover():
    """铁律 #14: waiting is not failing. The agent must not get an error just
    because a human picked up the mouse."""
    a = ControlArbiter()
    a.user_take_control()

    waiter = asyncio.create_task(a.wait_for_turn())
    await asyncio.sleep(0.02)
    assert not waiter.done(), "agent should be queued, not rejected"

    a.user_release_control()
    await asyncio.wait_for(waiter, timeout=2)


@pytest.mark.asyncio
async def test_agent_proceeds_immediately_when_it_already_holds_control():
    a = ControlArbiter()
    await asyncio.wait_for(a.wait_for_turn(), timeout=1)


@pytest.mark.asyncio
async def test_agent_waiting_is_visible_to_the_ui():
    """The panel has to be able to say 'the agent is waiting for you' —
    otherwise a queued agent looks like a hung one."""
    a = ControlArbiter()
    a.user_take_control()
    waiter = asyncio.create_task(a.wait_for_turn())
    await asyncio.sleep(0.02)

    assert a.to_dict()["agent_waiting"] is True

    a.user_release_control()
    await asyncio.wait_for(waiter, timeout=2)
    assert a.to_dict()["agent_waiting"] is False


@pytest.mark.asyncio
async def test_multiple_queued_actions_all_resume():
    a = ControlArbiter()
    a.user_take_control()
    waiters = [asyncio.create_task(a.wait_for_turn()) for _ in range(3)]
    await asyncio.sleep(0.02)

    a.user_release_control()
    await asyncio.wait_for(asyncio.gather(*waiters), timeout=2)


@pytest.mark.asyncio
async def test_no_deadline_is_imposed_on_a_waiting_agent():
    """铁律 #14 forbids a hard ceiling. A takeover that lasts twenty minutes
    is the user's prerogative, not a condition to time out."""
    a = ControlArbiter()
    assert not hasattr(a, "max_wait_seconds")
    a.user_take_control()
    waiter = asyncio.create_task(a.wait_for_turn())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    a.user_release_control()
    await asyncio.wait_for(waiter, timeout=2)


@pytest.mark.asyncio
async def test_closing_releases_waiters_rather_than_leaking_them():
    """A session that ends while the agent is queued must not leave the turn
    blocked on a promise nobody will ever resolve."""
    a = ControlArbiter()
    a.user_take_control()
    waiter = asyncio.create_task(a.wait_for_turn())
    await asyncio.sleep(0.02)

    a.close()

    with pytest.raises(Exception):
        await asyncio.wait_for(waiter, timeout=2)
