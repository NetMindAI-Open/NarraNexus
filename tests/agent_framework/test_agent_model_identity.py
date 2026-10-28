"""
@file_name: test_agent_model_identity.py
@author:
@date: 2026-07-10
@description: resolve_agent_model_identity resolves an agent's REAL
(framework, model) from the same slot overlay the runtime dispatches on.

Mirrors the overlay contract of ``_resolve_agent_framework_name``:
  1. per-agent agent_slots override wins ONLY with a provider_id;
  2. else the OWNER's user_slots default (keyed by agents.created_by);
  3. degrades to (nexus_power, "") on missing rows / DB hiccup.

Both framework AND model come from the winning slot row, and the
framework maps to a human display label.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from narranexus.platform.agent_framework.loop.driver import DEFAULT_AGENT_LOOP_FRAMEWORK
from narranexus.platform.agent_framework.providers.model_identity import (
    config_override_wins,
    effective_agent_slot,
    resolve_agent_config_slot,
    framework_of,
    slot_rebinds,
    resolve_agent_model_identity,
)


class _FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get_one(self, table, filters):
        for r in self.tables[table]:
            if all(r.get(k) == v for k, v in filters.items()):
                return r
        return None


class _BoomDB:
    async def get_one(self, table, filters):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_owner_default_framework_and_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.framework_display == "Codex CLI"
    assert ident.model == "gpt-5"


@pytest.mark.asyncio
async def test_per_agent_override_wins_framework_and_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "claude_code", "model": "claude-sonnet-4",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "p_x",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.model == "gpt-5"


@pytest.mark.asyncio
async def test_framework_only_stub_does_not_win():
    """An override with no provider_id doesn't rebind the slot, so the
    owner's user_slots framework + model win (same as the dispatcher)."""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "claude_code", "model": "claude-sonnet-4",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "claude_code"
    assert ident.framework_display == "Claude Agent SDK"
    assert ident.model == "claude-sonnet-4"


@pytest.mark.asyncio
async def test_override_with_provider_but_null_framework_does_not_win():
    """Regression (PR #84 review): agent_slots.agent_framework is nullable.
    An override with a provider_id but NULL agent_framework must NOT win —
    otherwise the driver runs the owner's framework (codex_cli) while this
    resolver would render the override's empty framework as Claude. Must
    fall through to the owner's user_slots, matching the dispatch-side
    ``_resolve_agent_framework_name``."""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "p_x",
        "agent_framework": None, "model": "gpt-5-override",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.framework_display == "Codex CLI"


@pytest.mark.asyncio
async def test_missing_rows_default_to_nexus_power_empty_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": ""})
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "nexus_power"
    assert ident.framework_display == "NexusPower-beta"
    assert ident.model == ""


@pytest.mark.asyncio
async def test_db_hiccup_degrades_gracefully():
    ident = await resolve_agent_model_identity("ag1", _BoomDB())
    assert ident.framework == "nexus_power"
    assert ident.model == ""


@pytest.mark.asyncio
async def test_unknown_framework_falls_back_to_raw_name():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "some_future_cli", "model": "m1",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    # Unknown canonical name is shown verbatim — never invent a brand.
    from narranexus.platform.agent_framework.loop.driver import available_agent_loop_frameworks
    assert "some_future_cli" not in available_agent_loop_frameworks()
    assert ident.framework_display == "some_future_cli"


# ── the pure overlay rule, shared with the agents directory ─────────────────


def test_slot_rebinds_needs_both_provider_and_framework():
    assert slot_rebinds({"provider_id": "p", "agent_framework": "codex_cli"}) is True
    assert slot_rebinds({"provider_id": "", "agent_framework": "codex_cli"}) is False
    assert slot_rebinds({"provider_id": "p", "agent_framework": None}) is False
    assert slot_rebinds(None) is False


def test_effective_agent_slot_falls_to_owner_default_for_a_stub():
    owner = {"agent_framework": "claude_code", "model": "m"}
    stub = {"provider_id": "", "agent_framework": "codex_cli"}
    assert effective_agent_slot(stub, owner) is owner
    full = {"provider_id": "p", "agent_framework": "codex_cli"}
    assert effective_agent_slot(full, owner) is full
    assert effective_agent_slot(None, None) is None


def test_framework_of_defaults_to_the_platform_default():
    """There is ONE accessor for "which framework": ``framework_of`` goes
    through ``resolve_framework_name``, so the identity overlay, the slot
    writer's validation and the driver cannot disagree. Reverting it to a local
    ``or DEFAULT_AGENT_FRAMEWORK`` literal makes the env/binding case below
    red."""
    assert DEFAULT_AGENT_LOOP_FRAMEWORK == "nexus_power"
    assert framework_of(None) == "nexus_power"
    assert framework_of({"agent_framework": None}) == "nexus_power"
    assert framework_of({"agent_framework": "codex_cli"}) == "codex_cli"


def test_framework_of_follows_the_resolver_not_a_local_literal(monkeypatch):
    monkeypatch.setenv("AGENT_LOOP_FRAMEWORK", "claude_code")
    assert framework_of(None) == "claude_code"
    assert framework_of({"agent_framework": None}) == "claude_code"
    # An explicit column still wins the precedence.
    assert framework_of({"agent_framework": "codex_cli"}) == "codex_cli"


def test_framework_of_never_raises_on_a_broken_binding(monkeypatch):
    """model_identity promises identity resolution can never break the
    system-prompt build, and ``resolve_framework_name`` can now raise
    ``FrameworkNotInstalledError`` on a misbinding."""
    from narranexus.platform.agent_framework.loop import driver as driver_mod

    def _boom() -> str:
        raise driver_mod.FrameworkNotInstalledError("acme_turbo")

    monkeypatch.setattr(driver_mod, "bound_default_framework", _boom)
    assert framework_of(None) == "nexus_power"
    assert framework_of({"agent_framework": "acme_turbo"}) == "acme_turbo"


# ── 2026-09-10: the PROVIDER overlay rule (#394 review I3) ────────────────────


def test_config_override_rule_is_provider_only_unlike_identity():
    """The two rules differ on purpose: a provider-only override (no
    agent_framework) routes the agent's CALLS to that provider, although it
    does not rebind the displayed identity."""
    provider_only = {"provider_id": "p2", "agent_framework": None}
    assert config_override_wins(provider_only) is True
    assert slot_rebinds(provider_only) is False
    assert config_override_wins({"provider_id": "", "agent_framework": "codex_cli"}) is False
    assert config_override_wins(None) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [
    ({"provider_id": "p_agent", "agent_framework": None}, "p_agent"),
    ({"provider_id": "", "agent_framework": "codex_cli"}, "p_owner"),
    (None, "p_owner"),
])
async def test_resolve_agent_config_slot_applies_the_provider_rule(override, expected):
    db = _FakeDB()
    db.tables["user_slots"].append(
        {"user_id": "owner", "slot_name": "agent", "provider_id": "p_owner"}
    )
    if override is not None:
        db.tables["agent_slots"].append({"agent_id": "a1", "slot_name": "agent", **override})
    slot = await resolve_agent_config_slot(db, agent_id="a1", user_id="owner")
    assert slot["provider_id"] == expected


def test_the_overlay_rule_has_no_hand_written_copies():
    """Every "which provider does this agent run on" consumer asks the one
    helper; none re-derives the agent_slots -> user_slots overlay."""
    import inspect

    from narranexus.platform.agent_framework.loop import circuit_breaker
    from narranexus.platform.agent_framework.providers import model_health
    from narranexus.platform.agent_framework.providers.driver import resolver

    for module in (circuit_breaker, model_health):
        src = inspect.getsource(module)
        assert '"agent_slots"' not in src, module.__name__
    assert "config_override_wins(row)" in inspect.getsource(resolver._apply_agent_overrides)
