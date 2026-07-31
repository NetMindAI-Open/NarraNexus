"""Diff/dedup logic for the model-probe sync engine."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from xyz_agent_context.agent_framework.providers import model_sync
from xyz_agent_context.agent_framework.providers.model_probe_ledger import PASS, FAIL


def _iso(days_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


FRESH = _iso(0)                # well inside the re-probe TTL
STALE = _iso(30)               # far past the re-probe TTL


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
        return model_sync.PROBE_OK  # everything probed passes (B's anthropic flips)

    monkeypatch.setattr(model_sync, "_probe", fake_probe)

    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "B": {"openai": PASS, "anthropic": FAIL, "tested_at": FRESH},
        "D": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
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
        # X works on openai only
        return model_sync.PROBE_OK if protocol == "openai" else model_sync.PROBE_MODEL_ERROR

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


# ---------------------------------------------------------------------------
# Stale-PASS revalidation (TTL re-probe) — pass entries are no longer trusted
# forever; only a definitive model error flips them to fail.
# ---------------------------------------------------------------------------

def _make_probe(monkeypatch, verdicts: dict[tuple[str, str], str]):
    """Install a fake probe answering per (model, protocol); records calls."""
    calls: list[tuple[str, str]] = []

    async def fake_probe(client, base, protocol, model, key):
        calls.append((model, protocol))
        return verdicts.get((model, protocol), model_sync.PROBE_OK)

    monkeypatch.setattr(model_sync, "_probe", fake_probe)
    return calls


def _netmind_catalog(monkeypatch, *mids: str):
    async def fake_catalog():
        return {m: {"display_name": m, "context": None} for m in mids}
    monkeypatch.setattr(model_sync, "_fetch_netmind_catalog", fake_catalog)


async def test_stale_pass_revalidated_and_flips_only_on_model_error(monkeypatch):
    _netmind_catalog(monkeypatch, "OLD", "NEW_ENOUGH")
    calls = _make_probe(monkeypatch, {
        ("OLD", "anthropic"): model_sync.PROBE_MODEL_ERROR,  # definitively dead
        ("OLD", "openai"): model_sync.PROBE_OK,              # still fine
    })
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "OLD": {"openai": PASS, "anthropic": PASS, "tested_at": STALE},
        "NEW_ENOUGH": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )

    led = ledger["sources"]["netmind"]["models"]
    # Fresh PASS entries are still trusted — no probe call.
    assert "NEW_ENOUGH" not in {m for m, _ in calls}
    # Stale PASS entries get revalidated on both protocols.
    assert ("OLD", "openai") in calls and ("OLD", "anthropic") in calls
    # Definitive model error flips pass -> fail; OK refreshes the clock.
    assert led["OLD"]["anthropic"] == FAIL
    assert led["OLD"]["openai"] == PASS
    assert led["OLD"]["tested_at"] != STALE
    assert "OLD" not in res.lists["anthropic"]
    assert "OLD" in res.lists["openai"]
    assert "OLD:anthropic" in res.flipped


async def test_stale_pass_transient_error_keeps_pass_and_retries_next_run(monkeypatch):
    _netmind_catalog(monkeypatch, "FLAKY")
    _make_probe(monkeypatch, {
        ("FLAKY", "openai"): model_sync.PROBE_TRANSIENT,   # 429 / 5xx / network
        ("FLAKY", "anthropic"): model_sync.PROBE_OK,
    })
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "FLAKY": {"openai": PASS, "anthropic": PASS, "tested_at": STALE},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )

    entry = ledger["sources"]["netmind"]["models"]["FLAKY"]
    # Transient failure must NOT flip a passing model (it would empty user
    # dropdowns on a rate-limit blip) — keep PASS, keep the old clock so the
    # next run retries.
    assert entry["openai"] == PASS
    assert entry["tested_at"] == STALE
    assert "FLAKY" in res.lists["openai"]
    assert res.flipped == []


async def test_revalidation_is_capped_oldest_first(monkeypatch):
    monkeypatch.setattr(model_sync, "_REVALIDATE_CAP", 2)
    _netmind_catalog(monkeypatch, "OLDEST", "OLDER", "OLD")
    calls = _make_probe(monkeypatch, {})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "OLDEST": {"openai": PASS, "anthropic": PASS, "tested_at": _iso(40)},
        "OLDER": {"openai": PASS, "anthropic": PASS, "tested_at": _iso(30)},
        "OLD": {"openai": PASS, "anthropic": PASS, "tested_at": _iso(20)},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )

    assert set(calls) == {("OLDEST", "openai"), ("OLDEST", "anthropic")}
    assert res.revalidated == 2


async def test_mass_definitive_failure_does_not_flip_anything(monkeypatch):
    # A dead/overdrawn platform key makes EVERY probe fail definitively.
    # Flipping all of them would wipe every user's dropdown — the guard keeps
    # the ledger untouched when a revalidation pass has zero OK results.
    mids = [f"M{i}" for i in range(6)]
    _netmind_catalog(monkeypatch, *mids)
    _make_probe(monkeypatch, {(m, "openai"): model_sync.PROBE_MODEL_ERROR for m in mids})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        m: {"openai": PASS, "tested_at": STALE} for m in mids
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )

    led = ledger["sources"]["netmind"]["models"]
    assert all(led[m]["openai"] == PASS for m in mids)
    assert res.flipped == []
    assert sorted(res.lists["openai"]) == sorted(mids)


async def test_empty_catalog_refuses_to_wipe(monkeypatch):
    async def empty_catalog():
        return {}
    monkeypatch.setattr(model_sync, "_fetch_netmind_catalog", empty_catalog)
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "tested_at": FRESH},
    }}}}

    with pytest.raises(RuntimeError):
        await model_sync.sync_source(
            "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
        )
    # Ledger untouched — A survives an upstream catalog outage.
    assert ledger["sources"]["netmind"]["models"]["A"]["openai"] == PASS


async def test_suspects_revalidated_regardless_of_ttl(monkeypatch):
    _netmind_catalog(monkeypatch, "SUS", "CLEAN")
    calls = _make_probe(monkeypatch, {
        ("SUS", "anthropic"): model_sync.PROBE_MODEL_ERROR,
        ("SUS", "openai"): model_sync.PROBE_OK,
    })
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "SUS": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "CLEAN": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
        suspects={("SUS", "anthropic"), ("SUS", "openai")},
    )

    # Runtime-reported suspects jump the TTL queue; untouched fresh entries don't.
    assert ("SUS", "anthropic") in calls
    assert "CLEAN" not in {m for m, _ in calls}
    assert ledger["sources"]["netmind"]["models"]["SUS"]["anthropic"] == FAIL
    assert "SUS:anthropic" in res.flipped


async def test_suspects_win_the_revalidation_cap(monkeypatch):
    # With the per-run cap at 1, the fresh suspect must be probed before the
    # older TTL-stale entry — a live user already hit the suspect.
    monkeypatch.setattr(model_sync, "_REVALIDATE_CAP", 1)
    _netmind_catalog(monkeypatch, "STALE_M", "SUS")
    calls = _make_probe(monkeypatch, {})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "STALE_M": {"openai": PASS, "anthropic": PASS, "tested_at": _iso(40)},
        "SUS": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}

    await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
        suspects={("SUS", "openai")},
    )
    assert calls == [("SUS", "openai")]


# ---------------------------------------------------------------------------
# Gateway extras: probed like catalog models, kept in the ledger, but never
# listed on netmind/system_pool cards (they are free-tier routing facts).
# ---------------------------------------------------------------------------

async def test_extra_models_probed_kept_and_excluded_from_card_lists(monkeypatch):
    _netmind_catalog(monkeypatch, "A")
    calls = _make_probe(monkeypatch, {("doubao/X", "anthropic"): model_sync.PROBE_MODEL_ERROR})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
        extra_models={"doubao/X": {"display_name": "doubao/X"}},
    )

    led = ledger["sources"]["netmind"]["models"]
    # Probed on both protocols, verdicts recorded, entry survives the
    # gone-from-catalog removal, and carries the extra marker.
    assert set(calls) == {("doubao/X", "openai"), ("doubao/X", "anthropic")}
    assert led["doubao/X"]["openai"] == PASS
    assert led["doubao/X"]["anthropic"] == FAIL
    assert led["doubao/X"]["extra"] is True
    # Never on the catalog card lists, even where it passes.
    assert "doubao/X" not in res.lists["openai"]
    assert res.lists["anthropic"] == ["A"]


async def test_extras_preserved_when_caller_does_not_pass_them(monkeypatch):
    # The manual "Update models" button calls sync_source without extras —
    # that must not purge the runner-maintained extra entries.
    _netmind_catalog(monkeypatch, "A")
    _make_probe(monkeypatch, {})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "doubao/X": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH, "extra": True},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
    )
    assert "doubao/X" in ledger["sources"]["netmind"]["models"]
    assert "doubao/X" not in res.removed
    assert "doubao/X" not in res.lists["openai"]


async def test_extra_marker_cleared_when_model_enters_catalog(monkeypatch):
    _netmind_catalog(monkeypatch, "doubao/X")
    _make_probe(monkeypatch, {})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "doubao/X": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH, "extra": True},
    }}}}

    res = await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
        extra_models={"doubao/X": {"display_name": "doubao/X"}},
    )
    assert not ledger["sources"]["netmind"]["models"]["doubao/X"].get("extra")
    assert "doubao/X" in res.lists["openai"]


# ---------------------------------------------------------------------------
# Free-tier entry: gateway list gated by netmind verdicts. FAIL hides a model;
# an unknown verdict keeps it (a probe outage must never empty the dropdown).
# ---------------------------------------------------------------------------

def test_build_free_tier_entry_filters_fails_and_keeps_unknowns():
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "B": {"openai": PASS, "anthropic": FAIL, "tested_at": FRESH},
        "dead": {"openai": FAIL, "anthropic": FAIL, "tested_at": FRESH},
    }}}}
    lists = model_sync.apply_free_tier_gate(
        ledger, ["A", "B", "dead", "unknown"]
    )
    assert lists["openai"] == ["A", "B", "unknown"]
    assert lists["anthropic"] == ["A", "unknown"]
    # Entry persisted for sync readers (provisioning's get_default_models).
    free_led = ledger["sources"]["netmind_free"]["models"]
    assert free_led["dead"]["openai"] == FAIL
    assert free_led["unknown"]["anthropic"] == PASS


async def test_refresh_free_tier_models_writes_gated_lists_per_protocol(monkeypatch):
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": FAIL, "tested_at": FRESH},
    }}}}

    class FakeWallet:
        async def served_models(self):
            return ["A", "unknown"]

    monkeypatch.setattr(
        model_sync, "_free_tier_wallet_client", lambda: FakeWallet()
    )

    updates: list[tuple[dict, list]] = []

    class FakeDB:
        async def update(self, table, filters, data):
            updates.append((filters, json.loads(data["models"])))
            return 1

    n = await model_sync.refresh_free_tier_models(FakeDB(), ledger=ledger)
    assert n == 2
    by_proto = {f["protocol"]: m for f, m in updates}
    assert by_proto["openai"] == ["A", "unknown"]
    assert by_proto["anthropic"] == ["unknown"]


# ---------------------------------------------------------------------------
# Drift report: what a human must reconcile between the gateway config and the
# upstream catalog. Transient trouble is invisible here by construction —
# verdicts only flip on definitive model errors.
# ---------------------------------------------------------------------------

def test_compute_drift_reports_failing_gateway_and_missing_catalog_passes():
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "new_pass": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
        "gw_dead": {"openai": FAIL, "anthropic": FAIL, "tested_at": FRESH, "extra": True},
        "half": {"openai": PASS, "anthropic": FAIL, "tested_at": FRESH},
    }}}}
    drift = model_sync.compute_drift(ledger, ["A", "gw_dead", "half"])
    # Only ALL-protocol failures are drift: "half" has no anthropic endpoint,
    # which is a perfectly normal single-protocol model, not dead config.
    assert drift["gateway_failing"] == ["gw_dead"]
    # extras never count as "missing from gateway" — they only exist there.
    assert drift["catalog_pass_not_in_gateway"] == ["new_pass"]


def test_compute_drift_empty_when_aligned():
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}
    drift = model_sync.compute_drift(ledger, ["A"])
    assert not drift["gateway_failing"] and not drift["catalog_pass_not_in_gateway"]


def test_get_default_models_netmind_free_prefers_gateway_gated_entry(monkeypatch):
    # Once the daily pass has written the netmind_free ledger entry, new free
    # cards must seed from it (gateway ∩ verdicts) — not from the raw netmind
    # catalog passes, which include models the gateway cannot route or price.
    from xyz_agent_context.agent_framework.providers import model_catalog
    from xyz_agent_context.agent_framework.providers import model_probe_ledger

    def fake_ledger_models(source, protocol):
        return {
            ("netmind_free", "openai"): ["gw_ok"],
            ("netmind", "openai"): ["gw_ok", "catalog_only"],
        }.get((source, protocol), [])

    monkeypatch.setattr(model_probe_ledger, "ledger_models", fake_ledger_models)
    assert model_catalog.get_default_models("netmind_free", "openai") == ["gw_ok"]
    # Entry absent (fresh install before the first daily pass) -> fall back to
    # the netmind mapping as before.
    def no_free_entry(source, protocol):
        return [] if source == "netmind_free" else ["cat"]
    monkeypatch.setattr(model_probe_ledger, "ledger_models", no_free_entry)
    assert model_catalog.get_default_models("netmind_free", "openai") == ["cat"]


# ---------------------------------------------------------------------------
# Review round (PR #201): the extra invariant must hold at EVERY read gate,
# transient never persists as FAIL, and lone-protocol gaps are not drift.
# ---------------------------------------------------------------------------

async def test_apply_ledger_to_db_never_lists_extras(monkeypatch):
    led = {"sources": {"netmind": {"models": {
        "A": {"openai": PASS, "anthropic": PASS},
        "gw_only": {"openai": PASS, "anthropic": PASS, "extra": True},
    }}}}
    monkeypatch.setattr(model_sync, "load_ledger", lambda: led)

    calls: list[tuple[str, str, list]] = []

    class FakeDB:
        async def update(self, table, filters, data):
            calls.append((filters["source"], filters["protocol"], json.loads(data["models"])))
            return 1

    await model_sync.apply_ledger_to_db(FakeDB(), sources=["netmind"])
    for _s, _p, models in calls:
        assert "gw_only" not in models
    assert any("A" in models for _s, _p, models in calls)


def test_ledger_models_never_lists_extras(monkeypatch):
    from xyz_agent_context.agent_framework.providers import model_probe_ledger

    led = {"sources": {"netmind": {"models": {
        "A": {"openai": PASS},
        "gw_only": {"openai": PASS, "extra": True},
    }}}}
    monkeypatch.setattr(model_probe_ledger, "load_ledger", lambda: led)
    assert model_probe_ledger.ledger_models("netmind", "openai") == ["A"]
    assert model_probe_ledger.ledger_models("system_pool", "openai") == ["A"]


async def test_new_model_transient_probe_stays_unknown_and_reprobes(monkeypatch):
    # First pass: the new model's probe hits transient trouble -> verdict must
    # stay ABSENT (unknown keeps it in the free dropdown), and the next pass
    # must probe it again even though it is no longer "new".
    _netmind_catalog(monkeypatch, "N")
    calls = _make_probe(monkeypatch, {
        ("N", "openai"): model_sync.PROBE_TRANSIENT,
        ("N", "anthropic"): model_sync.PROBE_TRANSIENT,
    })
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {}}}}
    await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )
    entry = ledger["sources"]["netmind"]["models"]["N"]
    assert "openai" not in entry and "anthropic" not in entry

    calls.clear()
    await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger
    )
    assert set(calls) == {("N", "openai"), ("N", "anthropic")}


def test_compute_drift_ignores_single_protocol_gaps():
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "openai_only": {"openai": PASS, "anthropic": FAIL, "tested_at": FRESH},
    }}}}
    drift = model_sync.compute_drift(ledger, ["openai_only"])
    assert drift["gateway_failing"] == []


async def test_extras_meta_does_not_clobber_known_display_name(monkeypatch):
    # A model that left the catalog but is still served by the gateway keeps
    # its human display_name — the bare-id extras meta only fills gaps.
    async def fake_catalog():
        return {"A": {"display_name": "A", "context": "1M"}}
    monkeypatch.setattr(model_sync, "_fetch_netmind_catalog", fake_catalog)
    _make_probe(monkeypatch, {})
    ledger = {"generated_at": None, "sources": {"netmind": {"models": {
        "gone/X": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH,
                    "display_name": "Nice Name", "extra": True},
        "A": {"openai": PASS, "anthropic": PASS, "tested_at": FRESH},
    }}}}
    await model_sync.sync_source(
        "netmind", keys={"openai": "k", "anthropic": "k"}, ledger=ledger,
        extra_models={"gone/X": {"display_name": "gone/X"}},
    )
    assert ledger["sources"]["netmind"]["models"]["gone/X"]["display_name"] == "Nice Name"
