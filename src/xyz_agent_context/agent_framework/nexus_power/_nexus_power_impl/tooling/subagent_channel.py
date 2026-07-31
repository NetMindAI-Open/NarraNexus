"""
@file_name: subagent_channel.py
@author: Bin Liang
@date: 2026-07-29
@description: Subagent channel (P4 seat) — the complete design, declared
as code.

Derivation rules on spawn: tool surface = parent's visible surface ∩
the spec's declared set (capability intersection); the parent's
PolicyEngine INSTANCE passes by reference (policy inheritance — fixing
the classic "hooks don't propagate" blind spot); prompts derive via
PromptMode.MINIMAL (never a second prompt); subagents are born mute
(empty expressive set) — results speak through the parent turn; child
thread ids derive from the parent (lineage keys, replayable pedigree).

Execution forms: synchronous wait (the spawn call resolves like any
tool call) and background (posthumous results: the child outlives the
parent turn via the runner process model; results re-enter through the
platform's announce service). No depth/count ceilings (iron rule #14);
recursion is observed by a repetition layer, not capped.
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class SubagentChannel:
    """spawn_subagent / agents_wait / agents_list as a ToolChannel (P4)."""

    def __init__(
        self,
        subagent_specs: dict[str, Any],
        policy_engine: Any,
        spawn_runner: Any,
        announce_endpoint: str | None,
    ) -> None:
        self._specs = subagent_specs
        self._policy = policy_engine
        self._spawn = spawn_runner
        self._announce = announce_endpoint

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError("SubagentChannel ships in P4")

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("SubagentChannel ships in P4")

    async def refresh(self) -> bool:
        raise NotImplementedError("SubagentChannel ships in P4")
