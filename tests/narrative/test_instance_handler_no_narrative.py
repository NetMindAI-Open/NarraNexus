"""
@file_name: test_instance_handler_no_narrative.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — narrative-independent dependency resolution.

`InstanceHandler.handle_completion` requires a `narrative_id`: it finds
dependents by scanning `instance_narrative_links` scoped to that narrative.
Jobs created via `/api/jobs/complex` never bind a narrative (the route never
passes `narrative_id`), so `handle_completion` can never see them — a
dependent Job's BLOCKED module_instance would stay BLOCKED forever no matter
how many of its dependencies completed.

`handle_completion_no_narrative` resolves directly from
`module_instances.dependencies` — the raw dependency graph every instance
already carries — with no narrative involvement at all.
"""
from __future__ import annotations

import pytest

from narranexus.platform.narrative._narrative_impl.instance_handler import InstanceHandler
from narranexus.platform.repository import InstanceRepository
from narranexus.platform.schema.instance_schema import InstanceStatus, ModuleInstanceRecord

AGENT_ID = "agent_1"


async def _seed_instance(db, instance_id, *, status, dependencies=None, module_class="JobModule"):
    repo = InstanceRepository(db)
    await repo.create_instance(ModuleInstanceRecord(
        instance_id=instance_id,
        module_class=module_class,
        agent_id=AGENT_ID,
        status=status,
        dependencies=dependencies or [],
    ))


@pytest.mark.asyncio
async def test_activates_blocked_dependent_when_its_only_dependency_completes(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="job_a", new_status=InstanceStatus.COMPLETED,
    )

    assert newly_activated == ["job_b"]
    repo = InstanceRepository(db_client)
    b = await repo.get_by_instance_id("job_b")
    assert b.status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_does_not_activate_when_a_sibling_dependency_is_still_pending(db_client):
    """Diamond shape: job_d depends on job_a AND job_b — completing only job_a
    must not unblock it."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.ACTIVE)
    await _seed_instance(
        db_client, "job_d", status=InstanceStatus.BLOCKED, dependencies=["job_a", "job_b"],
    )

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="job_a", new_status=InstanceStatus.COMPLETED,
    )

    assert newly_activated == []
    repo = InstanceRepository(db_client)
    d = await repo.get_by_instance_id("job_d")
    assert d.status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_activates_once_the_last_dependency_completes(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.ACTIVE)
    await _seed_instance(
        db_client, "job_d", status=InstanceStatus.BLOCKED, dependencies=["job_a", "job_b"],
    )

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="job_b", new_status=InstanceStatus.COMPLETED,
    )

    assert newly_activated == ["job_d"]


@pytest.mark.asyncio
async def test_a_failed_dependency_still_unblocks_a_dependent(db_client):
    """Matches the narrative-scoped path's existing semantics (membership in
    'history', not the specific outcome, satisfies a dependency) — a FAILED
    upstream still counts as resolved, not permanently stuck."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="job_a", new_status=InstanceStatus.FAILED,
    )

    assert newly_activated == ["job_b"]


@pytest.mark.asyncio
async def test_ignores_blocked_instances_belonging_to_a_different_agent(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    repo = InstanceRepository(db_client)
    await repo.create_instance(ModuleInstanceRecord(
        instance_id="job_other_agent",
        module_class="JobModule",
        agent_id="agent_other",
        status=InstanceStatus.BLOCKED,
        dependencies=["job_a"],
    ))

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="job_a", new_status=InstanceStatus.COMPLETED,
    )

    assert newly_activated == []


@pytest.mark.asyncio
async def test_unknown_completed_instance_is_a_noop(db_client):
    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    newly_activated = await handler.handle_completion_no_narrative(
        instance_id="does_not_exist", new_status=InstanceStatus.COMPLETED,
    )

    assert newly_activated == []


# ── review I10: periodic reconciliation for edges the poller never saw ────────

async def _blocked(db):
    """The caller's candidate page: every BLOCKED row, as the poller fetches it."""
    return await InstanceRepository(db).get_blocked_page(0, 1000)


@pytest.mark.asyncio
async def test_reconcile_judges_only_the_rows_it_is_handed(db_client):
    """Review r2 I-A: the handler must NOT re-query the table — the caller's
    keyset page is the bound on work per call. A resolvable BLOCKED row that
    is not in the handed-in page is left for the page that contains it."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await _seed_instance(db_client, "job_c", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)
    page = [r for r in await _blocked(db_client) if r.instance_id == "job_c"]

    assert await handler.reconcile_blocked_instances([]) == []
    assert await handler.reconcile_blocked_instances(page) == ["job_c"]
    b = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert b.status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_reconcile_activates_a_blocked_instance_whose_dependency_finished_unseen(db_client):
    """/api/jobs/complex creates jobs one at a time: the upstream can complete
    BEFORE the downstream's BLOCKED row exists, so no completion event ever
    unblocks it. The reconciliation must."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    assert await handler.reconcile_blocked_instances(await _blocked(db_client)) == ["job_b"]
    b = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert b.status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_reconcile_leaves_a_blocked_instance_with_a_live_dependency(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_c", status=InstanceStatus.FAILED)
    await _seed_instance(
        db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a", "job_c"],
    )

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    assert await handler.reconcile_blocked_instances(await _blocked(db_client)) == []
    b = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert b.status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_reconcile_leaves_a_blocked_instance_with_no_dependencies(db_client):
    """Nothing to wait for is an anomaly the scan cannot explain — never
    auto-run it."""
    await _seed_instance(db_client, "job_orphan", status=InstanceStatus.BLOCKED)

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    assert await handler.reconcile_blocked_instances(await _blocked(db_client)) == []


@pytest.mark.asyncio
async def test_reconcile_is_scoped_to_the_handlers_agent(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await InstanceRepository(db_client).create_instance(ModuleInstanceRecord(
        instance_id="job_other", module_class="JobModule", agent_id="agent_other",
        status=InstanceStatus.BLOCKED, dependencies=["job_a"],
    ))

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)

    assert await handler.reconcile_blocked_instances(await _blocked(db_client)) == []


@pytest.mark.asyncio
async def test_both_paths_share_the_activation_hook(db_client, monkeypatch):
    """M3: the event path and the reconciliation call ONE activation tail,
    which notifies the owning module (JobModule re-arms the job there)."""
    from narranexus.platform.module_system import module_registry

    seen = []

    class _FakeModule:
        @classmethod
        async def on_instance_activated(cls, instance_id, db):
            seen.append(instance_id)

    monkeypatch.setattr(module_registry, "get", lambda name: _FakeModule if name == "JobModule" else None)

    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await _seed_instance(db_client, "job_c", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_d", status=InstanceStatus.BLOCKED, dependencies=["job_c"])

    handler = InstanceHandler(agent_id=AGENT_ID)
    handler.set_database_client(db_client)
    await handler.handle_completion_no_narrative(instance_id="job_a", new_status=InstanceStatus.COMPLETED)
    await handler.reconcile_blocked_instances(await _blocked(db_client))

    assert sorted(seen) == ["job_b", "job_d"]
