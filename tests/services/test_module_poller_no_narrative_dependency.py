"""
@file_name: test_module_poller_no_narrative_dependency.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — ModulePoller must DISCOVER a completed narrative-less
instance (no instance_narrative_links row, e.g. a Job created via
/api/jobs/complex) and resolve its dependents via the narrative-independent
path (review r3 C1: the discovery query used to INNER JOIN a link, so this
path was unreachable). The reconciliation backstop judges narrative-less
BLOCKED instances only (review r3 I2).
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository import InstanceNarrativeLinkRepository, InstanceRepository
from narranexus.platform.schema.instance_schema import (
    InstanceStatus,
    LinkType,
    ModuleInstanceRecord,
)
from narranexus.platform.services.module_poller import CompletedInstanceInfo, ModulePoller

AGENT_ID = "agent_1"


async def _seed_instance(db, instance_id, *, status, dependencies=None):
    repo = InstanceRepository(db)
    await repo.create_instance(ModuleInstanceRecord(
        instance_id=instance_id,
        module_class="JobModule",
        agent_id=AGENT_ID,
        status=status,
        dependencies=dependencies or [],
    ))


def _make_poller(db_client) -> ModulePoller:
    poller = ModulePoller.__new__(ModulePoller)
    poller._db = db_client
    poller._instance_repo = None
    poller._link_repo = None
    return poller


@pytest.mark.asyncio
async def test_no_narrative_id_activates_dependent_via_no_narrative_path(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    # job_a is "completed" per module_instances (mirrors what JobTrigger's
    # raw-SQL _update_instance_completed already did before the poller runs).
    await InstanceRepository(db_client).update_status(
        "job_a", InstanceStatus.COMPLETED, completed_at=None,
    )

    poller = _make_poller(db_client)
    info = CompletedInstanceInfo(
        instance_id="job_a", narrative_id=None, agent_id=AGENT_ID,
        user_id=None, module_class="JobModule",
    )

    await poller._process_completed_instance(info)

    row = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert row.status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_narrative_id_present_still_uses_the_narrative_scoped_path(db_client, monkeypatch):
    """Regression guard: instances that DO have a narrative must keep using
    the existing handle_completion path, not silently switch over."""
    called = {}

    async def _fake_handle_completion(self, narrative_id, instance_id, new_status):
        called["narrative_id"] = narrative_id
        return []

    async def _fake_no_narrative(self, instance_id, new_status):
        called["no_narrative_called"] = True
        return []

    monkeypatch.setattr(
        "narranexus.platform.narrative.InstanceHandler.handle_completion",
        _fake_handle_completion,
    )
    monkeypatch.setattr(
        "narranexus.platform.narrative.InstanceHandler.handle_completion_no_narrative",
        _fake_no_narrative,
    )

    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await InstanceRepository(db_client).update_status(
        "job_a", InstanceStatus.COMPLETED, completed_at=None,
    )

    poller = _make_poller(db_client)
    info = CompletedInstanceInfo(
        instance_id="job_a", narrative_id="nar_1", agent_id=AGENT_ID,
        user_id=None, module_class="JobModule",
    )

    await poller._process_completed_instance(info)

    assert called.get("narrative_id") == "nar_1"
    assert "no_narrative_called" not in called


# ── review I10: the poll loop runs the BLOCKED reconciliation backstop ────────

@pytest.mark.asyncio
async def test_poll_cycle_reconciles_a_blocked_instance_whose_dependency_finished_unseen(db_client):
    """No completion event for job_a ever reaches the poller (it finished
    before job_b's BLOCKED row existed, or the event was lost to a restart).
    The first poll cycle's backstop must still unblock job_b."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    poller = ModulePoller(database_client=db_client)
    await poller._poll_and_enqueue()

    row = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert row.status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_reconcile_backstop_is_rate_limited(db_client):
    """It runs on the first cycle and then not again until the interval has
    elapsed — never on every 5-second poll."""
    poller = ModulePoller(database_client=db_client)
    await poller._poll_and_enqueue()
    first_stamp = poller._last_blocked_reconcile
    assert first_stamp is not None

    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await poller._poll_and_enqueue()

    assert poller._last_blocked_reconcile == first_stamp
    row = await InstanceRepository(db_client).get_by_instance_id("job_b")
    assert row.status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_reconcile_groups_by_agent_and_counts(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    repo = InstanceRepository(db_client)
    await repo.create_instance(ModuleInstanceRecord(
        instance_id="other_dep", module_class="JobModule", agent_id="agent_2",
        status=InstanceStatus.FAILED, dependencies=[],
    ))
    await repo.create_instance(ModuleInstanceRecord(
        instance_id="other_blocked", module_class="JobModule", agent_id="agent_2",
        status=InstanceStatus.BLOCKED, dependencies=["other_dep"],
    ))

    poller = ModulePoller(database_client=db_client)
    assert await poller._reconcile_blocked_instances() == 2
    assert (await repo.get_by_instance_id("other_blocked")).status == InstanceStatus.ACTIVE.value


# ── review r2 I-A: never-activatable rows must not starve the backstop ────────

@pytest.mark.asyncio
async def test_window_full_of_never_activatable_rows_still_activates_a_new_row(db_client, monkeypatch):
    """More permanently-stuck BLOCKED rows than one page holds (their
    dependency never becomes terminal), all OLDER than a freshly resolvable
    row. A fixed oldest-first window would only ever see the stuck rows; the
    keyset walk must still reach and activate the new one."""
    from narranexus.platform.services import module_poller as mp

    monkeypatch.setattr(mp, "_BLOCKED_RECONCILE_PAGE", 3)
    await _seed_instance(db_client, "upstream_ongoing", status=InstanceStatus.ACTIVE)
    for i in range(7):
        await _seed_instance(
            db_client, f"stuck_{i}", status=InstanceStatus.BLOCKED,
            dependencies=["upstream_ongoing"],
        )
    await _seed_instance(
        db_client, "stuck_deleted_dep", status=InstanceStatus.BLOCKED,
        dependencies=["instance_row_that_was_deleted"],
    )
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    await _seed_instance(db_client, "job_new", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    poller = ModulePoller(database_client=db_client)
    assert await poller._reconcile_blocked_instances() == 1

    repo = InstanceRepository(db_client)
    assert (await repo.get_by_instance_id("job_new")).status == InstanceStatus.ACTIVE.value
    for i in range(7):
        assert (await repo.get_by_instance_id(f"stuck_{i}")).status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_reconcile_pages_are_bounded_and_the_handler_does_not_requery(db_client, monkeypatch):
    """Every handler call receives at most one page of rows, and the handler
    never goes back to the table for more (the page is the real bound)."""
    from narranexus.platform.narrative import InstanceHandler
    from narranexus.platform.services import module_poller as mp

    monkeypatch.setattr(mp, "_BLOCKED_RECONCILE_PAGE", 2)
    await _seed_instance(db_client, "job_a", status=InstanceStatus.COMPLETED)
    for i in range(5):
        await _seed_instance(db_client, f"dep_{i}", status=InstanceStatus.BLOCKED, dependencies=["job_a"])

    sizes = []
    real = InstanceHandler.reconcile_blocked_instances

    async def _spy(self, blocked):
        sizes.append(len(blocked))
        return await real(self, blocked)

    async def _no_requery(self, *a, **kw):
        raise AssertionError("reconciliation must not re-query BLOCKED rows per agent")

    monkeypatch.setattr(InstanceHandler, "reconcile_blocked_instances", _spy)
    monkeypatch.setattr(InstanceRepository, "get_by_agent", _no_requery)

    poller = ModulePoller(database_client=db_client)
    assert await poller._reconcile_blocked_instances() == 5
    assert sizes and max(sizes) <= 2 and sum(sizes) == 5


# ── review r3 C1: the REAL discovery query must find narrative-less rows ──────

async def _finish_like_job_trigger(db, instance_id):
    """The two raw-SQL writes JobTrigger makes around a run
    (`_update_instance_for_execution` then `_update_instance_completed`)."""
    await db.execute(
        "UPDATE module_instances SET status = 'in_progress', last_polled_status = 'in_progress', "
        "callback_processed = FALSE WHERE instance_id = %s", (instance_id,), fetch=False,
    )
    await db.execute(
        "UPDATE module_instances SET status = 'completed', completed_at = %s "
        "WHERE instance_id = %s", ("2026-09-01 08:00:00", instance_id), fetch=False,
    )


async def _drain(poller):
    """One discovery pass, then process every discovered row — what the poll
    loop and its workers do, without the background tasks."""
    found = await poller._find_completed_instances()
    for info in found:
        await poller._process_completed_instance(info)
    return found


@pytest.mark.asyncio
async def test_completed_narrative_less_instance_is_discovered_and_unblocks_its_dependent(db_client):
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await _finish_like_job_trigger(db_client, "job_a")

    poller = ModulePoller(database_client=db_client)
    found = await _drain(poller)

    assert [(i.instance_id, i.narrative_id) for i in found] == [("job_a", None)]
    repo = InstanceRepository(db_client)
    assert (await repo.get_by_instance_id("job_b")).status == InstanceStatus.ACTIVE.value
    row = await db_client.get_one("module_instances", {"instance_id": "job_a"})
    assert row["callback_processed"] in (1, True)
    # The completion time JobTrigger wrote is kept, not rewritten to "now".
    assert str(row["completed_at"]).startswith("2026-09-01")
    # Processed once: the next pass finds nothing.
    assert await poller._find_completed_instances() == []


@pytest.mark.asyncio
async def test_poll_cycle_activates_the_dependent_without_the_reconcile_backstop(db_client):
    """The event path alone (backstop suppressed) unblocks the dependent on
    the next poll cycle — not 15 minutes later."""
    from datetime import datetime

    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "job_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await _finish_like_job_trigger(db_client, "job_a")

    poller = ModulePoller(database_client=db_client)
    poller._last_blocked_reconcile = datetime.now()
    await poller._poll_and_enqueue()
    info = poller._task_queue.get_nowait()
    assert (info.instance_id, info.narrative_id) == ("job_a", None)
    await poller._process_completed_instance(info)

    repo = InstanceRepository(db_client)
    assert (await repo.get_by_instance_id("job_b")).status == InstanceStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_discovery_keeps_narrative_bound_rows_on_the_narrative_path(db_client):
    """A row with an ACTIVE link is found once, with its narrative; a row whose
    only link is HISTORY is in neither half (unchanged behaviour)."""
    links = InstanceNarrativeLinkRepository(db_client)
    await _seed_instance(db_client, "bound", status=InstanceStatus.ACTIVE)
    await links.link("bound", "nar_1", link_type=LinkType.ACTIVE)
    await _seed_instance(db_client, "history_only", status=InstanceStatus.ACTIVE)
    await links.link("history_only", "nar_1", link_type=LinkType.HISTORY)
    await _seed_instance(db_client, "unlinked", status=InstanceStatus.ACTIVE)
    for iid in ("bound", "history_only", "unlinked"):
        await _finish_like_job_trigger(db_client, iid)

    found = await ModulePoller(database_client=db_client)._find_completed_instances()

    assert sorted((i.instance_id, i.narrative_id) for i in found) == [
        ("bound", "nar_1"), ("unlinked", None),
    ]


@pytest.mark.asyncio
async def test_narrative_free_completion_never_activates_a_narrative_bound_dependent(db_client):
    """Review r3 I2: a narrative-bound BLOCKED instance is judged only by
    handle_completion's link rule, even when its dependency is narrative-less
    and terminal."""
    await _seed_instance(db_client, "job_a", status=InstanceStatus.ACTIVE)
    await _seed_instance(db_client, "bound_b", status=InstanceStatus.BLOCKED, dependencies=["job_a"])
    await InstanceNarrativeLinkRepository(db_client).link("bound_b", "nar_1", link_type=LinkType.ACTIVE)
    await _finish_like_job_trigger(db_client, "job_a")

    await _drain(ModulePoller(database_client=db_client))

    row = await InstanceRepository(db_client).get_by_instance_id("bound_b")
    assert row.status == InstanceStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_reconcile_leaves_narrative_bound_blocked_instances_to_handle_completion(db_client):
    """Review r3 I2: status says the dependency is terminal but its link is
    still ACTIVE (handle_completion wrote status, then died before unlink).
    The link rule says "not satisfied"; the backstop must not overrule it —
    while a narrative-less sibling with the same dependency IS activated."""
    links = InstanceNarrativeLinkRepository(db_client)
    await _seed_instance(db_client, "dep", status=InstanceStatus.COMPLETED)
    await links.link("dep", "nar_1", link_type=LinkType.ACTIVE)
    await _seed_instance(db_client, "bound_b", status=InstanceStatus.BLOCKED, dependencies=["dep"])
    await links.link("bound_b", "nar_1", link_type=LinkType.ACTIVE)
    await _seed_instance(db_client, "free_b", status=InstanceStatus.BLOCKED, dependencies=["dep"])

    assert await ModulePoller(database_client=db_client)._reconcile_blocked_instances() == 1

    repo = InstanceRepository(db_client)
    assert (await repo.get_by_instance_id("bound_b")).status == InstanceStatus.BLOCKED.value
    assert (await repo.get_by_instance_id("free_b")).status == InstanceStatus.ACTIVE.value
