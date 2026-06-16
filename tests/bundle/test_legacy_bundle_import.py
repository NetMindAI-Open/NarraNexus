"""
@file_name: test_legacy_bundle_import.py
@author:
@date: 2026-06-11
@description: Regression tests for the 2026-06-11 legacy-bundle import bug.

A real v1.3.4 export (fixtures/legacy_v1_3_4_briefing_team.nxbundle,
6 agents / 56 narratives / 124 social entities) failed to import on
current NarraNexus for two independent reasons, each environment-
dependent:

  1. Fresh DBs (schema >= v1.7.16): bundle rows carry columns the
     unified-memory refactor REMOVED (narratives.embedding_updated_at)
     -> sqlite "no column" abort on the first narratives insert.
  2. Migrated old DBs (legacy columns still present, since auto_migrate
     only ever ADDS): import survives narratives, then dies
     reconstructing SocialNetworkEntity from rows whose list/dict
     fields are JSON STRINGS ('[]', '{}') -> pydantic ValidationError.

Both were amplified by confirm() being non-transactional: every failed
attempt stranded an orphan team + partial agents.

Covered here: schema sanitization, JSON-string field decoding, the full
real-bundle import round trip, mid-import rollback, and the composite
narrative-id rewrite (flagged as a low-risk concern in review).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from xyz_agent_context.bundle.importer import (
    _loads_maybe,
    _sanitize_for_schema,
    confirm,
    preflight,
)


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def isolated_db(tmp_path, tmp_workspace_root, monkeypatch):
    """Fresh sqlite AsyncDatabaseClient on a current-schema DB (the
    'fresh DB' environment from the bug matrix — no legacy columns)."""
    from xyz_agent_context.settings import settings as core_settings
    db_path = tmp_path / "test_nexus.db"
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{db_path}")

    from xyz_agent_context.utils import db_factory
    db_factory._clients_by_loop.clear()
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_v1_3_4_briefing_team.nxbundle"


# ── unit: schema sanitization ────────────────────────────────────────────────

def test_sanitize_drops_removed_columns_and_counts_them():
    dropped: dict = {}
    row = {
        "narrative_id": "nar_x", "agent_id": "agent_x",
        "embedding_updated_at": "2026-05-21T00:00:00",  # removed in v1.7.16
        "embedding": "[0.1, 0.2]",
    }
    clean = _sanitize_for_schema("narratives", row, dropped)
    assert "embedding_updated_at" not in clean
    assert "embedding" not in clean
    assert clean["narrative_id"] == "nar_x"
    assert dropped["narratives.embedding_updated_at"] == 1
    assert dropped["narratives.embedding"] == 1


def test_sanitize_passes_unknown_tables_through():
    dropped: dict = {}
    row = {"anything": 1}
    assert _sanitize_for_schema("not_a_real_table", row, dropped) == row
    assert dropped == {}


# ── unit: bundle JSON-string field decoding ──────────────────────────────────

@pytest.mark.parametrize(
    "value,default,expected",
    [
        ("[]", [], []),
        ('["a", "b"]', [], ["a", "b"]),
        ("{}", {}, {}),
        ('{"k": 1}', {}, {"k": 1}),
        (None, [], []),
        ("", {}, {}),
        ("not json", [], []),          # garbage -> default
        ('"just a string"', [], []),   # wrong decoded type -> default
        (["already", "list"], [], ["already", "list"]),  # structured passthrough
    ],
)
def test_loads_maybe(value, default, expected):
    assert _loads_maybe(value, default) == expected


# ── integration: the REAL legacy bundle imports end-to-end ───────────────────

@pytest.mark.asyncio
async def test_legacy_bundle_full_import(isolated_db):
    db = isolated_db
    await db.insert("users", {"user_id": "u_test", "password_hash": "x",
                              "role": "user", "user_type": "human"})

    pf = await preflight(FIXTURE, "u_test")
    assert pf["preflight_token"]

    res = await confirm(pf["preflight_token"], "u_test")

    assert res["agents_created"] == 6
    assert res["team_created"] is True
    assert res["narratives_created"] == 56
    assert res["social_entities_created"] > 0
    assert res["warnings"] == []
    # The v1.3.4 rows carried embedding-era columns; they must be counted,
    # not silently swallowed and not fatal.
    assert "dropped_legacy_columns" in res
    assert any(k.startswith("narratives.") for k in res["dropped_legacy_columns"])


# ── integration: mid-import failure leaves no orphans ────────────────────────

@pytest.mark.asyncio
async def test_failed_import_rolls_back_orphans(isolated_db):
    db = isolated_db
    await db.insert("users", {"user_id": "u_test", "password_hash": "x",
                              "role": "user", "user_type": "human"})

    pf = await preflight(FIXTURE, "u_test")

    # Sabotage a late write stage: social entity persistence explodes.
    with patch(
        "xyz_agent_context.repository.SocialNetworkRepository.save_entity",
        new=AsyncMock(side_effect=RuntimeError("boom mid-import")),
    ):
        with pytest.raises(RuntimeError, match="boom mid-import"):
            await confirm(pf["preflight_token"], "u_test")

    # No orphan team/agents/narratives survive the failure.
    assert await db.get("teams", {}) == []
    assert await db.get("agents", {}) == []
    assert await db.get("narratives", {}) == []
    assert await db.get("module_instances", {}) == []


# ── regression pin: composite narrative ids in free text ─────────────────────

def test_composite_narrative_id_rewrite_in_free_text():
    """Composite ids like agent_<hex>_<user>_default_N-01 embed an agent id.
    Free-text rewriting must not corrupt the composite by replacing only
    the embedded agent_<hex> substring with a different-length new id —
    or if it does rewrite, it must rewrite via the id_map consistently.
    This pins today's actual behavior so any change is a conscious one."""
    from xyz_agent_context.bundle import build_all_id_regex

    composite = "agent_a4e1a9d3eaec_binliang_default_N-01"
    text = json.dumps({"narrative_id": composite})
    rx = build_all_id_regex()

    matches = [m.group(0) for m in rx.finditer(text)]
    # The agent id embedded in the composite IS matched by the global
    # regex. The importer's id_map maps it to the NEW agent id, so the
    # composite stays internally consistent after rewrite (same map is
    # applied to the standalone agent_id column). Assert the match is
    # exactly the agent prefix — neither the whole composite nor a
    # partial hex fragment.
    assert matches == ["agent_a4e1a9d3eaec"]
