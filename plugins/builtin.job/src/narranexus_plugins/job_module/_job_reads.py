"""
@file_name: _job_reads.py
@author:
@date: 2026-08-10
@description: Job READ helpers shared by the AgentDataStore seam (DirectStore)
and the backend job routes (the HttpStore path).

The job_retrieval_by_id / _semantic / _by_keywords MCP tools already went
through `JobRepository` (dialect-safe, no raw SQL) — the migration just moves
their bodies here so the seam's DirectStore and the backend twin routes call
ONE implementation and return byte-identical output (parity by a single shared
function, not two hand-kept copies). Each function returns the COMPLETE result
dict and never raises. All three are agent-scoped: by_id checks
`job.agent_id == agent_id`; the two searches pass `agent_id` to the repository
so results are already the caller's own.
"""
from __future__ import annotations

from typing import List, Optional

from loguru import logger

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus
from narranexus_plugins.job_module._job_response import job_to_llm_dict


async def fetch_job_by_id(db, agent_id: str, job_id: str) -> dict:
    """Full detail of one job by id, scoped to the owning agent."""
    try:
        job = await JobRepository(db).get_job(job_id)
        if not job:
            return {"success": False, "error": f"Job not found: {job_id}"}
        if job.agent_id != agent_id:
            return {"success": False, "error": "Access denied: Job belongs to a different agent"}
        return {
            "success": True,
            "job": {
                **job_to_llm_dict(job),
                "process": job.process,
                "last_error": job.last_error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            },
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.fetch_job_by_id] failed: {e}")
        return {"success": False, "error": str(e)}


async def search_jobs_semantic(
    db, agent_id: str, query: str, user_id: Optional[str], status: Optional[str], limit: int
) -> dict:
    """Keyword (BM25) job search — the `job_retrieval_semantic` tool's body.
    (Vectors were retired; the tool name stays for the agent-facing contract.)"""
    try:
        status_enum = None
        if status:
            try:
                status_enum = JobStatus(status.lower())
            except ValueError:
                valid = ", ".join(s.value for s in JobStatus)
                return {"success": False, "error": f"Invalid status: {status}. Valid values: {valid}"}
        results = await JobRepository(db).search_keyword(
            agent_id=agent_id, query=query, user_id=user_id, status=status_enum, limit=limit,
        )
        jobs_data = [
            {**job_to_llm_dict(job), "similarity_score": round(score, 4)}
            for job, score in results
        ]
        return {"success": True, "query": query, "total_results": len(jobs_data), "jobs": jobs_data}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.search_jobs_semantic] failed: {e}")
        return {"success": False, "error": str(e)}


async def search_jobs_by_keywords(
    db, agent_id: str, keywords: List[str], user_id: Optional[str], status: Optional[str], limit: int
) -> dict:
    """Keyword-list job search — the `job_retrieval_by_keywords` tool's body."""
    try:
        status_enum = None
        if status:
            try:
                status_enum = JobStatus(status.lower())
            except ValueError:
                return {"success": False, "error": f"Invalid status: {status}"}
        jobs = await JobRepository(db).search_by_keywords(
            agent_id=agent_id, keywords=keywords, user_id=user_id, status=status_enum, limit=limit,
        )
        jobs_data = []
        for job in jobs:
            entry = job_to_llm_dict(job)
            if len(entry["description"] or "") > 200:
                entry["description"] = entry["description"][:200] + "..."
            jobs_data.append(entry)
        return {"success": True, "keywords": keywords, "total_results": len(jobs_data), "jobs": jobs_data}
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.search_jobs_by_keywords] failed: {e}")
        return {"success": False, "error": str(e)}
