"""
@file_name: test_bus_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: MessageBusTrigger._process_agent circuit-breaker skip-gate.

A paused/cooling agent must be skipped WITHOUT consuming its pending bus
messages (they stay queued for when it resumes).
"""
from __future__ import annotations

import asyncio

import pytest

import xyz_agent_context.agent_framework.agent_circuit_breaker as cb
from xyz_agent_context.message_bus.message_bus_trigger import MessageBusTrigger


class _SpyBus:
    """Records whether the trigger tried to read pending messages."""
    def __init__(self):
        self.get_pending_called = False

    async def get_pending_messages(self, agent_id):
        self.get_pending_called = True
        return []


def _trigger(bus):
    t = MessageBusTrigger.__new__(MessageBusTrigger)
    t._semaphore = asyncio.Semaphore(10)
    t._agent_locks = {}
    t._bus = bus
    return t


@pytest.mark.asyncio
async def test_paused_agent_skipped_without_touching_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (True, "paused:auth")
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_agent("ag_paused")

    assert result is False
    assert bus.get_pending_called is False  # messages left queued


@pytest.mark.asyncio
async def test_healthy_agent_falls_through_to_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return (False, None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_agent("ag_ok")

    # No pending messages → returns False, but it DID consult the bus.
    assert result is False
    assert bus.get_pending_called is True
