"""
@file_name: test_hook_persist_turn.py
@author: Bin Liang
@date: 2026-05-20
@description: HookManager.hook_persist_turn — the synchronous, next-turn-critical
persistence phase (short-reply amnesia fix). Runs each module's hook_persist_turn
inside the request (before background hooks); a single module's failure is
non-fatal so it never crashes the turn.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from xyz_agent_context.module.hook_manager import HookManager


def _module(name: str, persist=None):
    m = MagicMock()
    m.config.name = name
    m.hook_persist_turn = persist or AsyncMock()
    return m


async def test_invokes_each_module():
    hm = HookManager()
    a, b = _module("A"), _module("B")
    params = MagicMock()
    await hm.hook_persist_turn([a, b], params)
    a.hook_persist_turn.assert_awaited_once_with(params)
    b.hook_persist_turn.assert_awaited_once_with(params)


async def test_one_failure_is_non_fatal():
    hm = HookManager()
    boom = _module("boom", AsyncMock(side_effect=RuntimeError("kaboom")))
    ok = _module("ok")
    params = MagicMock()
    # Must not raise — a failed persist hook must not crash the turn.
    await hm.hook_persist_turn([boom, ok], params)
    ok.hook_persist_turn.assert_awaited_once()


async def test_empty_list_is_noop():
    hm = HookManager()
    await hm.hook_persist_turn([], MagicMock())  # no error
