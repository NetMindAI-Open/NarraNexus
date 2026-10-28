"""
@file_name: wait_channel.py
@author: Bin Liang
@date: 2026-08-23
@description: The ``wait_for_input`` control tool — the agent proactively
HOLDS the turn open for new input instead of ending it.

Motivating case: a team-room agent that has done its part and expects a
teammate (or the user) to reply shortly. Without this it must end the turn
and be re-triggered from scratch when the reply lands; with it, it says
"I'll wait", the loop blocks on the steering inlet up to N seconds, and the
moment a message arrives it rides the next model step (fusion is immediate).

Two outcomes only (the Owner's contract): an input-arrival (the loop
injects it and continues) or a timeout (nothing within N seconds → the loop tells
the agent so it can wrap up or wait again).

This channel NEVER blocks: its ``call`` only records the request on the
shared ``WaitState``; the loop reads it at the step boundary and does the
blocking wait itself (the loop owns the inlet and the cancellation signal).
This mirrors ``SchedulingChannel``'s plan handoff — a channel signals the
loop through shared state rather than reaching into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)

#: Clamp bounds for the requested wait. A wait costs real wall-clock and holds
#: a worker slot, so it is bounded; a missing/garbage value falls back to the
#: default rather than erroring (fail-soft — the agent asked to wait, honour it).
MIN_WAIT_SECONDS = 1.0
MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0


def _clamp_seconds(raw: Any) -> float:
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_WAIT_SECONDS
    if secs != secs:  # NaN
        return DEFAULT_WAIT_SECONDS
    return max(MIN_WAIT_SECONDS, min(MAX_WAIT_SECONDS, secs))


@dataclass
class WaitState:
    """One turn's pending wait request. ``pending`` is the CLAMPED seconds the
    agent asked for; the loop reads-and-clears it at the boundary. Mutable and
    per-turn, like ``PlanState`` — the channel writes, the loop reads, no
    back-reference either way. Satisfies the ``WaitRequest`` protocol."""

    pending: Optional[float] = None

    def request(self, raw: Any) -> float:
        """Clamp ``raw`` seconds into [MIN, MAX] (missing/garbage → default) and
        record it — the ONE write path, so the bound has a single home. Every
        producer (WaitChannel today, the cloud executor's /steer tomorrow) gets
        it for free instead of re-clamping; the loop can then trust ``pending``
        is bounded. Returns the clamped value for the caller's reply text."""
        self.pending = _clamp_seconds(raw)
        return self.pending


class WaitChannel:
    """``wait_for_input`` as a ToolChannel. Records the request; never blocks."""

    def __init__(self, state: WaitState) -> None:
        self._state = state

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="wait_for_input",
                description=(
                    "Hold this turn open and WAIT for new input instead of "
                    "ending. Use it when you have done your part for now but "
                    "expect a teammate or the user to reply shortly, and you "
                    "want to act on their reply in THIS turn rather than end and "
                    "be woken later. The turn pauses up to `seconds` seconds; the "
                    "moment a new message arrives you continue and see it. If "
                    "nothing arrives in time you are told so and can wrap up (or "
                    "wait again). Do NOT use it to poll or to stall — only when a "
                    "reply is genuinely expected soon."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "seconds": {
                            "type": "integer",
                            "description": (
                                f"How long to wait, {int(MIN_WAIT_SECONDS)}–"
                                f"{int(MAX_WAIT_SECONDS)}s "
                                f"(default {int(DEFAULT_WAIT_SECONDS)})."
                            ),
                        },
                    },
                },
            )
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != "wait_for_input":
            return ToolResult(call_id="", ok=False, error=f"unknown tool {name!r}")
        secs = self._state.request(args.get("seconds", DEFAULT_WAIT_SECONDS))
        return ToolResult(
            call_id="",
            ok=True,
            content=(
                f"Waiting up to {int(secs)}s for new input — you will continue "
                f"the moment something arrives, or be told if nothing does."
            ),
        )
