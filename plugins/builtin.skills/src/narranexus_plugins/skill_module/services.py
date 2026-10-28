"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-04
@description: Services builtin.skills exposes on the process locator (``skills.workspaces``).

The bundle importer, the marketplace install pipeline / registry and the
skill sync service get an agent's ``SkillWorkspace`` through this factory
instead of importing ``SkillModule``.
"""
from __future__ import annotations

from typing import Optional

from narranexus.contracts.services import SKILL_WORKSPACES


def skill_workspace(agent_id: str, user_id: Optional[str]):
    from narranexus_plugins.skill_module import SkillModule

    return SkillModule(agent_id=agent_id, user_id=user_id)


SERVICES = ((SKILL_WORKSPACES, skill_workspace),)

__all__ = ["SERVICES", "skill_workspace"]
