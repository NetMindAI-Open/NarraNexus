"""
@file_name: skills_channel.py
@author: Bin Liang
@date: 2026-07-29
@description: Skills channel (P3 seat) — agentskills.io-standard skill
directories with progressive disclosure (index resident, bodies via
``skill_view``) and an agent self-authoring loop guarded by provenance
(agent-authored skills are auto-prunable; user-authored ones are
structurally immune).

Honest seat: not assembled in v1; the class exists so the extension
path is code, not folklore. Mounting it before implementation raises.
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolContext,
    ToolResult,
    ToolSpec,
)


class SkillsChannel:
    """skill_view / skill_manage as a ToolChannel (P3)."""

    def __init__(self, skill_dirs: tuple[str, ...]) -> None:
        self._skill_dirs = skill_dirs

    def list_tools(self) -> list[ToolSpec]:
        raise NotImplementedError("SkillsChannel ships in P3")

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError("SkillsChannel ships in P3")

    async def refresh(self) -> bool:
        raise NotImplementedError("SkillsChannel ships in P3")
