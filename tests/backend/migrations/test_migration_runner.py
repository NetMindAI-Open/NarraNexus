"""
@file_name: test_migration_runner.py
@author: Bin Liang
@date: 2026-06-09
@description: The versioned migration runner — applies pending migrations in
order, exactly once, layer-by-layer across version jumps, best-effort on failure.
"""
import json

import pytest

from backend import migrations as mig
from backend.migrations import Migration, run_pending_migrations


def _mk(mid, calls, *, fail=False, ret=None):
    async def _fn(db):
        calls.append(mid)
        if fail:
            raise RuntimeError(f"boom {mid}")
        return ret or {"n": 1}
    return Migration(mid, f"test {mid}", _fn)


@pytest.mark.asyncio
async def test_applies_in_order_and_records(db_client, monkeypatch):
    calls = []
    monkeypatch.setattr(mig, "REGISTRY", [_mk("0001_a", calls), _mk("0002_b", calls)])

    res = await run_pending_migrations(db_client)

    assert calls == ["0001_a", "0002_b"]
    assert set(res) == {"0001_a", "0002_b"}
    ledger = {r["migration_id"] for r in await db_client.get("schema_migrations")}
    assert ledger == {"0001_a", "0002_b"}


@pytest.mark.asyncio
async def test_run_once_skips_applied(db_client, monkeypatch):
    calls = []
    monkeypatch.setattr(mig, "REGISTRY", [_mk("0001_a", calls)])

    await run_pending_migrations(db_client)
    await run_pending_migrations(db_client)  # second startup

    assert calls == ["0001_a"], "migration must run exactly once"


@pytest.mark.asyncio
async def test_cross_version_runs_only_pending_in_order(db_client, monkeypatch):
    # DB already at 0001 (e.g. an old v1.7 install); jumping ahead runs 0002+0003.
    await db_client.insert("schema_migrations", {"migration_id": "0001_a", "app_version": "1.7.0"})
    calls = []
    monkeypatch.setattr(mig, "REGISTRY", [
        _mk("0001_a", calls), _mk("0002_b", calls), _mk("0003_c", calls),
    ])

    await run_pending_migrations(db_client)

    assert calls == ["0002_b", "0003_c"], "skip applied, apply the rest in order"


@pytest.mark.asyncio
async def test_failure_stops_chain_and_is_not_recorded(db_client, monkeypatch):
    calls = []
    monkeypatch.setattr(mig, "REGISTRY", [
        _mk("0001_ok", calls),
        _mk("0002_boom", calls, fail=True),
        _mk("0003_after", calls),
    ])

    await run_pending_migrations(db_client)  # must NOT raise

    assert calls == ["0001_ok", "0002_boom"], "chain stops at the failure"
    ledger = {r["migration_id"] for r in await db_client.get("schema_migrations")}
    assert ledger == {"0001_ok"}, "failed migration not recorded → retries next startup"


@pytest.mark.asyncio
async def test_notes_capture_stats(db_client, monkeypatch):
    monkeypatch.setattr(mig, "REGISTRY", [_mk("0001_a", [], ret={"agents": 3, "indexed": 7})])
    await run_pending_migrations(db_client)
    row = await db_client.get_one("schema_migrations", {"migration_id": "0001_a"})
    assert json.loads(row["notes"]) == {"agents": 3, "indexed": 7}
    assert row["app_version"]
