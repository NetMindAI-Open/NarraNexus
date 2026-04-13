"""
@file_name: test_dashboard_fetchers.py
@description: T16 — async DB aggregation helpers.
"""
import pytest

from backend.routes._dashboard_helpers import (
    fetch_enhanced_signals,
    fetch_instances,
    fetch_jobs,
    fetch_last_activity,
)


@pytest.mark.asyncio
async def test_fetch_last_activity_uses_max_per_agent(tmp_db_with_events):
    ctx = tmp_db_with_events
    out = await fetch_last_activity([ctx["agent_a"], ctx["agent_b"], ctx["agent_c"]])
    # MySQL returns naive datetime so ISO strings round-trip may lose tz;
    # verify by prefix (date + time seconds) rather than exact equality.
    def _prefix(ts: str) -> str:
        # "2026-04-13T07:27:23..." → first 19 chars
        return ts[:19] if ts else ts
    assert _prefix(out[ctx["agent_a"]]) == _prefix(ctx["latest_ts_a"])
    assert _prefix(out[ctx["agent_b"]]) == _prefix(ctx["latest_ts_b"])
    assert out[ctx["agent_c"]] is None


@pytest.mark.asyncio
async def test_fetch_jobs_partitions_running_vs_pending(tmp_db_with_jobs):
    """v2.1.1: each state has its raw count — no overlap, no duplicates.

    Fixture seeds 3 jobs: 1 running + 1 pending + 1 active. Previously
    'pending' was a union (counted as 2); now it's raw pending only (1).
    """
    agent_a = tmp_db_with_jobs["agent_a"]
    out = await fetch_jobs([agent_a])
    assert len(out[agent_a]["running"]) == 1
    assert len(out[agent_a]["pending"]) == 1
    assert len(out[agent_a]["active"]) == 1
    assert out[agent_a]["running"][0]["title"] == "running1"
    assert out[agent_a]["running"][0]["description"] == "desc for running1"
    # Anti-regression: the same job_id MUST NOT appear in two state buckets.
    seen: dict[str, str] = {}
    for state in ("running", "active", "pending", "blocked", "paused", "failed"):
        for j in out[agent_a].get(state, []):
            assert j["job_id"] not in seen, (
                f"DUPLICATE: {j['job_id']} in both {seen[j['job_id']]} and {state}"
            )
            seen[j["job_id"]] = state


@pytest.mark.asyncio
async def test_fetch_instances_only_in_progress(tmp_empty_db):
    import uuid
    db = tmp_empty_db
    suffix = uuid.uuid4().hex[:8]
    agent_a = f"agent_i_{suffix}"
    try:
        await db.execute(
            "DELETE FROM module_instances WHERE agent_id LIKE %s",
            (f"agent_i_{suffix}%",),
            fetch=False,
        )
    except Exception:
        pass
    await db.insert("module_instances", {
        "instance_id": f"i1_{suffix}", "module_class": "ChatModule",
        "agent_id": agent_a, "status": "in_progress",
        "description": "running step X",
    })
    await db.insert("module_instances", {
        "instance_id": f"i2_{suffix}", "module_class": "JobModule",
        "agent_id": agent_a, "status": "completed",
    })
    out = await fetch_instances([agent_a, f"agent_none_{suffix}"])
    assert len(out[agent_a]) == 1 and out[agent_a][0]["instance_id"] == f"i1_{suffix}"
    assert out[f"agent_none_{suffix}"] == []


@pytest.mark.asyncio
async def test_fetch_enhanced_has_all_four_fields(tmp_empty_db):
    import uuid
    agent = f"agent_enh_{uuid.uuid4().hex[:8]}"
    out = await fetch_enhanced_signals([agent])
    assert set(out[agent].keys()) == {
        "recent_errors_1h", "token_rate_1h",
        "active_narratives", "unread_bus_messages",
    }
    assert out[agent]["token_rate_1h"] is None


@pytest.mark.asyncio
async def test_fetch_empty_agent_ids_returns_empty():
    assert await fetch_last_activity([]) == {}
    assert await fetch_jobs([]) == {}
    assert await fetch_instances([]) == {}
    assert await fetch_enhanced_signals([]) == {}
