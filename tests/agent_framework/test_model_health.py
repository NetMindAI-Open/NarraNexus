"""DB ledger carrier + runtime model-suspect feedback (providers/model_health)."""
import json

from xyz_agent_context.agent_framework.providers import model_health
from xyz_agent_context.agent_framework.providers.model_probe_ledger import (
    PASS,
    FAIL,
    load_ledger_db,
    save_ledger_db,
)


# ---------------------------------------------------------------------------
# Ledger DB carrier
# ---------------------------------------------------------------------------

async def test_ledger_db_round_trip(db_client):
    assert await load_ledger_db(db_client) is None  # empty table -> file fallback

    ledger = {"generated_at": "2026-07-30T00:00:00+00:00", "sources": {
        "netmind": {"models": {
            "A": {"openai": PASS, "anthropic": FAIL, "tested_at": "t1"},
        }},
        "openrouter": {"models": {
            "B": {"openai": PASS, "tested_at": "t2"},
        }},
    }}
    assert await save_ledger_db(db_client, ledger) == 2

    loaded = await load_ledger_db(db_client)
    assert loaded == ledger

    # Upsert: a second save with changed verdicts overwrites, no dup rows.
    ledger["sources"]["netmind"]["models"]["A"]["anthropic"] = PASS
    ledger["generated_at"] = "2026-07-31T00:00:00+00:00"
    assert await save_ledger_db(db_client, ledger) == 2
    loaded = await load_ledger_db(db_client)
    assert loaded["sources"]["netmind"]["models"]["A"]["anthropic"] == PASS
    assert loaded["generated_at"] == "2026-07-31T00:00:00+00:00"
    rows = await db_client.get("model_probe_ledger", filters={})
    assert len(rows) == 2


async def test_ledger_db_skips_corrupt_rows(db_client):
    await db_client.insert("model_probe_ledger", {
        "source": "netmind", "models_json": "{not json", "generated_at": "x",
    })
    await db_client.insert("model_probe_ledger", {
        "source": "openrouter",
        "models_json": json.dumps({"models": {"B": {"openai": PASS}}}),
        "generated_at": "y",
    })
    loaded = await load_ledger_db(db_client)
    assert set(loaded["sources"]) == {"openrouter"}


# ---------------------------------------------------------------------------
# Suspects
# ---------------------------------------------------------------------------

async def test_report_load_clear_suspects(db_client):
    ok = await model_health.report_model_suspect(
        db_client, source="netmind", protocol="anthropic",
        model="dead/model", reason="model_not_found",
    )
    assert ok
    # Duplicate report -> occurrences bump, still one row.
    await model_health.report_model_suspect(
        db_client, source="netmind", protocol="anthropic",
        model="dead/model", reason="model_not_found",
    )
    # system_pool normalizes onto netmind (same backend / ledger entry).
    await model_health.report_model_suspect(
        db_client, source="system_pool", protocol="openai",
        model="other/model", reason="model_not_found",
    )
    # Out-of-scope sources have no probe path -> not recorded.
    assert not await model_health.report_model_suspect(
        db_client, source="claude_oauth", protocol="anthropic",
        model="opus", reason="model_not_found",
    )
    assert not await model_health.report_model_suspect(
        db_client, source="user", protocol="openai",
        model="gpt-x", reason="model_not_found",
    )

    suspects = await model_health.load_suspects(db_client)
    assert suspects == {"netmind": {("dead/model", "anthropic"), ("other/model", "openai")}}
    row = await db_client.get_one("model_probe_suspects", {
        "source": "netmind", "model_id": "dead/model", "protocol": "anthropic",
    })
    assert int(row["occurrences"]) == 2

    await model_health.clear_suspects(db_client, "netmind")
    assert await model_health.load_suspects(db_client) == {}


# ---------------------------------------------------------------------------
# Agent-slot resolution (the step_3 error-path entry point)
# ---------------------------------------------------------------------------

async def _seed_binding(db, *, source="netmind", protocol="anthropic",
                        model="dead/model", provider_id="prov_test01"):
    await db.insert("user_providers", {
        "provider_id": provider_id, "user_id": "u1", "name": "NetMind",
        "source": source, "protocol": protocol, "auth_type": "api_key",
        "api_key": "k", "base_url": "", "models": json.dumps([model]),
    })
    await db.insert("user_slots", {
        "user_id": "u1", "slot_name": "agent",
        "provider_id": provider_id, "model": model,
    })


async def test_report_agent_slot_suspect_via_user_slot(db_client):
    await _seed_binding(db_client)
    ok = await model_health.report_agent_slot_suspect(
        db_client, user_id="u1", agent_id="agt_1", reason="model_not_found",
    )
    assert ok
    assert await model_health.load_suspects(db_client) == {
        "netmind": {("dead/model", "anthropic")}
    }


async def test_report_agent_slot_suspect_agent_override_wins(db_client):
    await _seed_binding(db_client)
    await db_client.insert("user_providers", {
        "provider_id": "prov_ovr01", "user_id": "u1", "name": "OpenRouter",
        "source": "openrouter", "protocol": "anthropic", "auth_type": "api_key",
        "api_key": "k2", "base_url": "", "models": json.dumps(["ovr/model"]),
    })
    await db_client.insert("agent_slots", {
        "agent_id": "agt_1", "slot_name": "agent",
        "provider_id": "prov_ovr01", "model": "ovr/model",
    })
    await model_health.report_agent_slot_suspect(
        db_client, user_id="u1", agent_id="agt_1", reason="model_not_found",
    )
    assert await model_health.load_suspects(db_client) == {
        "openrouter": {("ovr/model", "anthropic")}
    }


async def test_report_agent_slot_suspect_oauth_slot_not_recorded(db_client):
    # A claude_oauth binding has no probe path — must be a no-op, not an error.
    await _seed_binding(db_client, source="claude_oauth", model="opus",
                        provider_id="prov_cc0001")
    ok = await model_health.report_agent_slot_suspect(
        db_client, user_id="u1", agent_id="agt_1", reason="model_not_found",
    )
    assert not ok
    assert await model_health.load_suspects(db_client) == {}


async def test_report_agent_slot_suspect_no_binding(db_client):
    assert not await model_health.report_agent_slot_suspect(
        db_client, user_id="nobody", agent_id="agt_x", reason="model_not_found",
    )
