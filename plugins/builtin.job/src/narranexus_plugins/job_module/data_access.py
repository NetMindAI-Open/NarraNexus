"""
@file_name: data_access.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.job's AgentDataStore bodies: job reads (by id / semantic / keywords) and writes (create / update / pause / cancel).

Registered into ``agent.capabilities.data_access`` by the builtin.job manifest
Handlers take the
store's db client first; module internals are imported inside each handler so
registering the contribution stays free of the module's import cost.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.data_access import DataAccessSpec
from narranexus.kernel.plugins.registry import Contribution


async def job_retrieval_by_id(db: Any, agent_id: str, job_id: str) -> dict:
    from narranexus_plugins.job_module import fetch_job_by_id

    return await fetch_job_by_id(db, agent_id, job_id)


async def job_retrieval_semantic(db: Any, agent_id: str, query: str, user_id: Optional[str], status: Optional[str], limit: int) -> dict:
    from narranexus_plugins.job_module import search_jobs_semantic

    return await search_jobs_semantic(db, agent_id, query, user_id, status, limit)


async def job_retrieval_by_keywords(db: Any, agent_id: str, keywords: list, user_id: Optional[str], status: Optional[str], limit: int) -> dict:
    from narranexus_plugins.job_module import search_jobs_by_keywords

    return await search_jobs_by_keywords(db, agent_id, keywords, user_id, status, limit)


async def job_update(db: Any, agent_id: str, job_id: str, fields: dict) -> dict:
    from narranexus_plugins.job_module import update_job_from_args

    return await update_job_from_args(db, agent_id, job_id, **fields)


async def job_create(db: Any, agent_id: str, fields: dict) -> dict:
    from narranexus_plugins.job_module import create_job_from_args

    return await create_job_from_args(db, agent_id, **fields)


async def job_pause(db: Any, agent_id: str, job_id: str) -> dict:
    from narranexus_plugins.job_module import pause_job_from_args

    return await pause_job_from_args(db, agent_id, job_id)


async def job_cancel(db: Any, agent_id: str, job_id: str) -> dict:
    from narranexus_plugins.job_module import cancel_job_from_args

    return await cancel_job_from_args(db, agent_id, job_id)


_HANDLERS = {
    "job_retrieval_by_id": job_retrieval_by_id,
    "job_retrieval_semantic": job_retrieval_semantic,
    "job_retrieval_by_keywords": job_retrieval_by_keywords,
    "job_update": job_update,
    "job_create": job_create,
    "job_pause": job_pause,
    "job_cancel": job_cancel,
}
DATA_ACCESS = tuple(Contribution(n, (lambda n=n, h=h: DataAccessSpec(n, h))) for n, h in _HANDLERS.items())

__all__ = ["DATA_ACCESS", *_HANDLERS]
