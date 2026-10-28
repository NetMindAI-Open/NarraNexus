"""
@file_name: _job_response.py
@author: Bin Liang
@date: 2026-04-21
@description: Shape Job records into LLM-facing dicts.

Spec: 2026-04-21-job-timezone-redesign

The UTC fields next_run_time / last_run_time are poller-internal and MUST NOT
appear in any LLM-facing tool response. LLM sees only user-local beta views
(next_run_at / last_run_at) plus the IANA timezone label for clarity.
"""
from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from narranexus.platform.schema.job_schema import JobModel


def job_to_llm_dict(job: "JobModel") -> Dict[str, Any]:
    """
    Convert a JobModel into a dict suitable for MCP tool responses.

    EXCLUDES next_run_time and last_run_time (UTC physical instants — poller
    internals). EXPOSES next_run_at / last_run_at (user-local naive ISO) plus
    the IANA timezone label for unambiguous rendering.
    """
    return {
        "job_id": job.job_id,
        "agent_id": job.agent_id,
        "user_id": job.user_id,
        "instance_id": job.instance_id,
        "title": job.title,
        "description": job.description,
        "payload": job.payload,
        "job_type": job.job_type.value,
        "trigger_config": (
            job.trigger_config.model_dump(exclude_none=True)
            if job.trigger_config else None
        ),
        "status": job.status.value,
        "notification_method": job.notification_method,
        "next_run_at": job.next_run_at_local,
        "timezone": job.next_run_tz,
        "last_run_at": job.last_run_at_local,
        "last_run_timezone": job.last_run_tz,
        "related_entity_id": getattr(job, "related_entity_id", None),
        "narrative_id": getattr(job, "narrative_id", None),
        "iteration_count": getattr(job, "iteration_count", 0),
    }
