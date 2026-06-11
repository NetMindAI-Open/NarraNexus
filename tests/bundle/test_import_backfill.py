"""
@file_name: test_import_backfill.py
@author: Bin Liang
@date: 2026-06-09
@description: Bundle import must rebuild the unified-memory SEARCH INDEXES.

The importer raw-inserts operational rows, bypassing the projection-write points
(crud._index_narrative / step_4 interaction / create_job / send_message). So a
freshly imported agent's narrative/job/bus/interaction were invisible to
`remember` until re-touched. `_backfill_search_indexes` re-projects them at the
end of import — covers BOTH old bundles (which predate the indexes) and current
ones (same raw-insert path). Scoped to this import's records (a fresh agent_id).
"""
import json

import pytest

from xyz_agent_context.memory.backfill import backfill_agent_search_indexes
from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine
import xyz_agent_context.memory.specs  # noqa: F401 — registers kinds

AGENT = "agent_backfill01"


async def _seed(db):
    await db.insert("narratives", {
        "narrative_id": "nar_bf01", "type": "chat", "agent_id": AGENT,
        "narrative_info": json.dumps({
            "name": "Apex 并购对账",
            "current_summary": "讨论 Apex 收购案 380 万差额，张伟核对银行流水。",
            "description": "",
        }),
        "topic_keywords": json.dumps(["并购", "对账", "Apex", "差额"]),
        "round_counter": 0,
    })
    await db.insert("instance_jobs", {
        "instance_id": "job_inst_bf", "job_id": "job_bf01", "agent_id": AGENT,
        "user_id": "test_user", "title": "核对 Apex 银行流水对账",
        "description": "逐笔比对 2024 Q1 预付款台账", "job_type": "scheduled",
        "status": "active",
    })
    await db.insert("bus_messages", {
        "message_id": "msg_bf01", "channel_id": "ch_bf", "from_agent": AGENT,
        "content": "来自巨灵神：Apex 差额报表已生成，请对账模块复核。",
        "msg_type": "text", "created_at": "2026-06-09T00:00:00+00:00",
    })
    await db.insert("events", {
        "event_id": "evt_bf01", "trigger": "chat", "trigger_source": "test",
        "agent_id": AGENT, "user_id": "test_user", "narrative_id": "nar_bf01",
        "env_context": json.dumps({"input": "Apex 对账有进展吗？"}),
        "final_output": "已定位 380 万差额来自未入账预付款，张伟在核对流水。",
    })


@pytest.mark.asyncio
async def test_backfill_rebuilds_all_projection_indexes(db_client):
    await _seed(db_client)

    n = await backfill_agent_search_indexes(db_client, AGENT)
    assert n >= 4, f"expected ≥4 records indexed, got {n}"

    for kind in ("narrative", "job", "bus", "event"):
        rows = await db_client.get(f"memory_{kind}", {"agent_id": AGENT})
        assert rows, f"memory_{kind} not backfilled"
        assert rows[0].get("source_ref"), f"memory_{kind} row missing source_ref pointer"


@pytest.mark.asyncio
async def test_backfilled_records_are_findable_by_remember(db_client):
    await _seed(db_client)
    await backfill_agent_search_indexes(db_client, AGENT)

    coord = MemoryCoordinator(MemoryEngine(db_client, AGENT))
    hits = await coord.remember("Apex 对账 380 万差额", limit=10)
    kinds = {h.kind for h in hits}

    assert {"narrative", "job", "bus", "event"} <= kinds, (
        f"remember did not surface all backfilled kinds, got {kinds}"
    )


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_client):
    await _seed(db_client)
    first = await backfill_agent_search_indexes(db_client, AGENT)
    second = await backfill_agent_search_indexes(db_client, AGENT)
    assert first == second
    # deterministic record_id ⇒ re-run upserts the same rows, no duplicates
    rows = await db_client.get("memory_narrative", {"agent_id": AGENT})
    assert len(rows) == 1


# ── version stamping ──────────────────────────────────────────────────────────
def test_app_version_is_real_not_stale():
    """__version__ must reflect the installed package (pyproject anchor), not the
    old hardcoded 0.1.0; the builder stamps that same live version into manifests."""
    from xyz_agent_context import __version__
    from xyz_agent_context.bundle.builder import _current_app_version
    assert __version__ not in ("0.1.0", "1.3.4")
    assert _current_app_version() == __version__
