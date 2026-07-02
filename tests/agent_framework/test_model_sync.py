"""Diff/dedup logic for the model-probe sync engine."""
import json

import pytest

from xyz_agent_context.agent_framework import model_sync
from xyz_agent_context.agent_framework.model_probe_ledger import PASS, FAIL


async def test_sync_dedup_and_overwrite(monkeypatch):
    # Catalog: A (already passed both), B (previously failed anthropic),
    # C (brand new); D is NOT in the catalog (removed upstream).
    async def fake_catalog():
        return {
            "A": {"display_name": "A", "context": "1M"},
            "B": {"display_name": "B", "context": "1M"},
            "C": {"display_name": "C", "context": "1M"},
        }
    monkeypatch.setattr(model_sync, "_fetch_netmind_catalog", fake_catalog)

    probe_calls: list[tuple[str, str]] = []

    async def fake_probe(client, base, protocol, model, key):
        probe_calls.append((model, protocol))
        return True  # everything probed this run passes (B's anthropic flips)

    monkeypatch.setattr(model_sync, "_probe", fake_probe)

    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": "x"},
        "B": {"openai": PASS, "anthropic": FAIL, "tested_at": "x"},
        "D": {"openai": PASS, "anthropic": PASS, "tested_at": "x"},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )

    # Dedup: A (passed both) is NOT re-probed; B re-probes ONLY its failed
    # protocol; C (new) probes both.
    assert set(probe_calls) == {("B", "anthropic"), ("C", "openai"), ("C", "anthropic")}
    assert "A" not in {m for m, _ in probe_calls}

    # Overwrite: D dropped from ledger + lists.
    assert "D" in res.removed
    assert "D" not in ledger["sources"]["netmind"]["models"]
    assert "D" not in res.lists["openai"]

    # B's anthropic flipped fail -> pass on re-probe.
    assert ledger["sources"]["netmind"]["models"]["B"]["anthropic"] == PASS

    # New model present on both lists; existing passers retained.
    assert "C" in res.lists["openai"] and "C" in res.lists["anthropic"]
    assert {"A", "B", "C"} <= set(res.lists["anthropic"])
    assert res.added == ["C"]
    assert res.probed == 3


async def test_system_pool_maps_to_netmind(monkeypatch):
    async def fake_catalog():
        return {"X": {"display_name": "X", "context": None}}
    monkeypatch.setattr(model_sync, "_fetch_netmind_catalog", fake_catalog)

    async def fake_probe(client, base, protocol, model, key):
        return protocol == "openai"  # X works on openai only

    monkeypatch.setattr(model_sync, "_probe", fake_probe)

    ledger = {"generated_at": None, "sources": {}}
    res = await model_sync.sync_source(
        "system_pool", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )
    # system_pool writes under the shared "netmind" ledger entry
    assert "netmind" in ledger["sources"]
    assert res.lists["openai"] == ["X"]
    assert res.lists["anthropic"] == []


async def test_apply_ledger_to_db_overwrites_all_rows(monkeypatch):
    led = {"sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS},
        "B": {"openai": PASS, "anthropic": FAIL},
    }}}}
    monkeypatch.setattr(model_sync, "load_ledger", lambda: led)

    calls: list[tuple[str, str, list]] = []

    class FakeDB:
        async def update(self, table, filters, data):
            assert table == "user_providers"
            calls.append((filters["source"], filters["protocol"], json.loads(data["models"])))
            return 1

    await model_sync.apply_ledger_to_db(FakeDB(), sources=["netmind"])
    by = {(s, p): models for s, p, models in calls}
    # openai passers = [A, B]; anthropic passers = [A]
    assert by[("netmind", "openai")] == ["A", "B"]
    assert by[("netmind", "anthropic")] == ["A"]
    # system_pool rows overwritten from the same netmind ledger entry
    assert by[("system_pool", "openai")] == ["A", "B"]
    assert by[("system_pool", "anthropic")] == ["A"]
