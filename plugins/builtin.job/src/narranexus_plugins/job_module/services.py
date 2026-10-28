"""
@file_name: services.py
@author: Bin Liang
@date: 2026-09-04
@description: Services builtin.job exposes on the process locator (``jobs.instances``, ``jobs.run_once``, ``jobs.resume_for_principal``).

Onboarding / Arena provisioning create jobs, the Manyfold sync route runs
one on demand and admin reinstate resumes the jobs a suspension paused; they resolve these through ``narranexus.contracts.services`` so
the platform imports nothing from the job module.
"""
from __future__ import annotations

from typing import Any, Iterable

from narranexus.contracts.services import JOB_INSTANCES, JOB_RESUME_FOR_PRINCIPAL, JOB_RUN_ONCE


def job_instances(db: Any):
    from narranexus_plugins.job_module.job_service import JobInstanceService

    return JobInstanceService(db)


async def run_once(agent_id: str, job_id: str):
    from narranexus_plugins.job_module.run_once import run_job_once

    return await run_job_once(agent_id, job_id)


async def resume_for_principal(db: Any, user_id: str, paused_reasons: Iterable[str]) -> int:
    from narranexus_plugins.job_module.job_recovery import resume_jobs_paused_for_principal

    return await resume_jobs_paused_for_principal(db, user_id, paused_reasons)


SERVICES = (
    (JOB_INSTANCES, job_instances),
    (JOB_RUN_ONCE, run_once),
    (JOB_RESUME_FOR_PRINCIPAL, resume_for_principal),
)

__all__ = ["SERVICES", "job_instances", "resume_for_principal", "run_once"]
