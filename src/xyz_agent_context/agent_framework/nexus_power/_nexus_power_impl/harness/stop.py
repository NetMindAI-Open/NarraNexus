"""
@file_name: stop.py
@author: Bin Liang
@date: 2026-07-29
@description: Stop policies. Iron rule #14: no turn/duration ceilings —
stopping is always semantic (no more actions / goal reached), never a
counter.
"""

from __future__ import annotations

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_power.contracts.protocols import LedgerView
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import ToolCall


class NoMoreActionsStop:
    """v1 default: the turn ends when a step produced zero tool calls.

    The monologue divergence from every other harness: "stopped talking"
    is not a stop condition here — only "stopped acting" is. Talking
    does not keep a turn alive.
    """

    async def should_stop(
        self, step_calls: Sequence[ToolCall], ledger: LedgerView
    ) -> bool:
        return not step_calls


class GoalSpecStop:
    """P4 seat: goal-based adjudication by a cheap judge model. Declared
    so the extension path is visible; assembling it in v1 is an error."""

    async def should_stop(
        self, step_calls: Sequence[ToolCall], ledger: LedgerView
    ) -> bool:
        raise NotImplementedError("GoalSpecStop ships in P4")
