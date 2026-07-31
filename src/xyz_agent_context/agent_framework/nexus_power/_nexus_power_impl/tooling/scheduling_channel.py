"""
@file_name: scheduling_channel.py
@author: Bin Liang
@date: 2026-07-29
@description: Scheduling channel — the loop's own scheduling primitives
as tools.

``update_plan`` (shipped): the agent's plan is a full snapshot replaced
on every write — the simplest semantics a model handles reliably (no
patch protocol to get wrong). Two consumers, one write:
  - the ui track gets a plan event, so a frontend renders live progress;
  - the prompt's plan section re-injects the current plan EVERY step,
    the one placement compaction can never eat (the durable-rules
    consensus).

``sleep`` (P4 seat): self-scheduling — the turn closes with
``EndReason.SUSPENDED`` and the control plane's timer wakes it, so a
dead container never loses the wake-up.
"""

from __future__ import annotations

from typing import Any, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)

_ALLOWED_STATUS = ("pending", "in_progress", "completed")

PlanSink = Callable[[list[dict[str, Any]], str], None]


class PlanState:
    """The current plan (one per turn) — read by the prompt assembler,
    written by the tool. Deliberately tiny: steps plus a note."""

    __slots__ = ("steps", "note")

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.note: str = ""

    def replace(self, steps: list[dict[str, Any]], note: str) -> None:
        self.steps = steps
        self.note = note

    def render(self) -> str:
        """The prompt's plan block (empty until the agent writes one)."""
        if not self.steps:
            return ""
        marks = {"completed": "x", "in_progress": ">", "pending": " "}
        lines = [
            f"- [{marks.get(str(s.get('status', 'pending')), ' ')}] {s.get('step', '')}"
            for s in self.steps
        ]
        return (
            "Your current plan (keep it updated with update_plan):\n"
            + "\n".join(lines)
        )


class SchedulingChannel:
    """update_plan (live) / sleep (P4 seat) as a ToolChannel."""

    def __init__(self, plan: PlanState, emit_plan: PlanSink) -> None:
        self._plan = plan
        self._emit_plan = emit_plan

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="update_plan",
                description=(
                    "Record or update your plan for this task. Send the "
                    "COMPLETE step list every time (it replaces the previous "
                    "plan). Use it for multi-step work: write the plan before "
                    "you start, then call again to flip a step to "
                    "'in_progress' or 'completed' as you go. Exactly one step "
                    "should be 'in_progress' at a time. Skip it for trivial "
                    "one-step requests."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "The complete ordered step list.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step": {
                                        "type": "string",
                                        "description": "Short imperative description.",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": list(_ALLOWED_STATUS),
                                    },
                                },
                                "required": ["step", "status"],
                            },
                        },
                        "note": {
                            "type": "string",
                            "description": "Optional one-line reason for this update.",
                        },
                    },
                    "required": ["steps"],
                },
            )
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != "update_plan":
            return ToolResult(call_id="", ok=False, error=f"unknown tool {name!r}")
        raw_steps = args.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return ToolResult(
                call_id="", ok=False, error="`steps` must be a non-empty array"
            )
        steps: list[dict[str, Any]] = []
        for item in raw_steps:
            if not isinstance(item, dict) or not str(item.get("step", "")).strip():
                return ToolResult(
                    call_id="", ok=False, error="each step needs a non-empty `step`"
                )
            status = str(item.get("status", "pending"))
            if status not in _ALLOWED_STATUS:
                status = "pending"
            steps.append({"step": str(item["step"]).strip(), "status": status})
        note = str(args.get("note", "")).strip()
        self._plan.replace(steps, note)
        self._emit_plan(steps, note)
        done = sum(1 for s in steps if s["status"] == "completed")
        return ToolResult(
            call_id="",
            ok=True,
            content=f"plan updated: {done}/{len(steps)} steps completed",
        )

    async def refresh(self) -> bool:
        return False
