"""
@file_name: test_instance_sync_job_blocked_status.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — InstanceSyncService.create_jobs_for_instances must
create a dependent Job with JobStatus.BLOCKED (mirroring the ModuleInstance's
own `_set_initial_status` decision), not the default PENDING — otherwise
get_due_jobs() (PENDING/ACTIVE only) fires it immediately regardless of its
unmet dependency, the same root cause as the /api/jobs/complex path (B-16).
"""
from __future__ import annotations

import pytest

from narranexus.platform.module_system._module_impl.instance_decision import (
    InstanceDict,
    JobConfig,
)
from narranexus.platform.services.instance_sync_service import InstanceSyncService

AGENT_ID = "agent_1"
USER_ID = "user_1"


@pytest.mark.asyncio
async def test_dependent_job_is_created_blocked(db_client):
    service = InstanceSyncService(db_client)

    instances = [
        InstanceDict(
            task_key="upstream",
            module_class="JobModule",
            job_config=JobConfig(title="Upstream"),
        ),
        InstanceDict(
            task_key="downstream",
            module_class="JobModule",
            depends_on=["upstream"],
            job_config=JobConfig(title="Downstream"),
        ),
    ]

    processed, key_to_id = await service.process_instance_decision(
        instances, agent_id=AGENT_ID, user_id=USER_ID,
    )
    job_ids = await service.create_jobs_for_instances(
        processed, agent_id=AGENT_ID, user_id=USER_ID, key_to_id=key_to_id,
    )

    assert len(job_ids) == 2
    upstream_row = await db_client.get_one(
        "instance_jobs", {"instance_id": key_to_id["upstream"]},
    )
    downstream_row = await db_client.get_one(
        "instance_jobs", {"instance_id": key_to_id["downstream"]},
    )
    assert upstream_row["status"] == "pending"
    assert downstream_row["status"] == "blocked"
