"""
@file_name: plugin_services.py
@author: Bin Liang
@date: 2026-09-04
@description: Platform-side accessors for services builtins expose (skills workspace, job instances, run-once, resume-for-principal).

Thin wrappers over ``KERNEL_REGISTRIES.services`` + ``contracts.services``
so the bundle importer, marketplace, skill sync, onboarding and Arena
provisioning never name a builtin module.

The services are registered by the manifest loader when the HOST BOOTS — that
is the only registration path; importing this module, or the providing plugin,
registers nothing. A process that did not boot therefore has an EMPTY locator,
and that case is deliberately indistinguishable from "the owning builtin is
disabled in this distribution": both raise ``UnknownEntry`` from ``require``,
loudly and at the call site. Callers that have a degraded path use the ``try_*``
variants and get ``None``.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.services import (
    JOB_INSTANCES,
    JOB_RESUME_FOR_PRINCIPAL,
    JOB_RUN_ONCE,
    SKILL_WORKSPACES,
)
from narranexus.contracts.skill import SkillWorkspace


def _services():
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES.services


def skill_workspace(agent_id: str, user_id: Optional[str]) -> SkillWorkspace:
    """An agent's skills workspace (builtin.skills); raises UnknownEntry when skills are disabled."""
    return _services().require(SKILL_WORKSPACES)(agent_id, user_id)


def job_instances(db: Any) -> Any:
    """The job instance service over ``db`` (builtin.job); raises UnknownEntry when jobs are disabled."""
    return _services().require(JOB_INSTANCES)(db)


def try_job_run_once() -> Any:
    """``async (agent_id, job_id) -> JobRunOutcome`` or None when builtin.job is disabled."""
    return _services().try_require(JOB_RUN_ONCE)


def try_job_resume_for_principal() -> Any:
    """``async (db, user_id, paused_reasons) -> int`` or None when builtin.job is disabled."""
    return _services().try_require(JOB_RESUME_FOR_PRINCIPAL)


__all__ = ["job_instances", "skill_workspace", "try_job_resume_for_principal", "try_job_run_once"]
