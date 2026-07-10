"""
@file_name: test_step_3_framework_dispatch.py
@date: 2026-05-29
@description: Tests for ``_resolve_agent_framework_name`` in
step_3_agent_loop.py — the per-user dispatch from
``user_slots.agent_framework`` to a framework name string that's
then handed to the ``get_agent_loop_driver`` registry.

The dispatch indirection was reshaped during the CodexSDKv2 work:

* Pre-v2: ``_resolve_agent_framework_sdk`` returned an SDK class
  directly from a static ``_AGENT_FRAMEWORK_SDK_MAP`` dict; unknown
  names were silently rewritten to ClaudeAgentSDK.
* Post-v2: ``_resolve_agent_framework_name`` returns the raw string;
  unknown names are NOT rewritten here — they pass through to
  ``get_agent_loop_driver`` which raises ``ValueError`` so typos
  surface at the dispatch site instead of masquerading as "claude".
  The registry is keyed by framework name and supports plug-in
  registration (claude_code / codex_cli / codex_cli_v2 / codex_official),
  matching binding rule #9 (hot-pluggable, no tight binding).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework import (
    ClaudeAgentSDK,
    CodexSDK,
    get_agent_loop_driver,
)
from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _resolve_agent_framework_name,
)


class _FakeDB:
    """Table-aware stand-in for AsyncDatabaseClient.

    ``_resolve_agent_framework_name`` is now keyed by agent_id and resolves
    framework from the OWNER's user_slots, honouring a per-agent agent_slots
    override that actually rebinds the slot (has a provider_id). Seed those
    three tables here.
    """

    def __init__(self, *, owner_framework=None, override=None, owner="u1",
                 agent_id="ag1"):
        self.calls: list[tuple] = []
        self.tables: dict[str, list[dict]] = {
            "agents": [], "user_slots": [], "agent_slots": []
        }
        if owner is not None:
            self.tables["agents"].append(
                {"agent_id": agent_id, "created_by": owner}
            )
            row = {"user_id": owner, "slot_name": "agent"}
            if owner_framework is not None:
                row["agent_framework"] = owner_framework
            self.tables["user_slots"].append(row)
        if override is not None:
            self.tables["agent_slots"].append(
                {"agent_id": agent_id, "slot_name": "agent", **override}
            )

    async def get_one(self, table, filters):
        self.calls.append((table, dict(filters)))
        for r in self.tables.get(table, []):
            if all(r.get(k) == v for k, v in filters.items()):
                return r
        return None


class _DeadDB:
    """DB that always raises on get_one — for the error-fallback test."""

    async def get_one(self, table, filters):
        raise RuntimeError("simulated DB failure")


# ----- registry surface --------------------------------------------


def test_registry_resolves_claude_code_to_claude_agent_sdk(tmp_path):
    driver = get_agent_loop_driver(
        framework="claude_code", working_path=str(tmp_path)
    )
    assert isinstance(driver, ClaudeAgentSDK)


def test_registry_resolves_codex_cli_to_codex_sdk_v2(tmp_path):
    """Cutover 2026-06-08: ``codex_cli`` now resolves to ``CodexSDKv2``.
    The v1 ``CodexSDK`` class is still importable (revival fallback)
    but no longer registered."""
    from xyz_agent_context.agent_framework import CodexSDKv2

    driver = get_agent_loop_driver(
        framework="codex_cli", working_path=str(tmp_path)
    )
    assert isinstance(driver, CodexSDKv2)
    assert not isinstance(driver, CodexSDK)


# ----- happy paths -------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_codex_when_owner_chose_codex():
    db = _FakeDB(owner_framework="codex_cli")
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "codex_cli"


@pytest.mark.asyncio
async def test_dispatch_returns_claude_when_owner_chose_claude():
    db = _FakeDB(owner_framework="claude_code")
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "claude_code"


@pytest.mark.asyncio
async def test_dispatch_returns_codex_cli_v2_verbatim():
    """v2 framework names must pass through verbatim so the registry
    can route them to CodexSDKv2."""
    db = _FakeDB(owner_framework="codex_cli_v2")
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "codex_cli_v2"


@pytest.mark.asyncio
async def test_dispatch_per_agent_override_wins():
    """A per-agent override that rebinds the agent slot (has a provider)
    overrides the owner default framework."""
    db = _FakeDB(
        owner_framework="claude_code",
        override={"provider_id": "p_x", "agent_framework": "codex_cli"},
    )
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "codex_cli"


# ----- fallback paths ----------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_row_missing():
    """Owner with no user_slots agent row → claude_code default."""
    db = _FakeDB(owner_framework=None)
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "claude_code"


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_owner_missing():
    """Agent with no owner row → defensive claude_code."""
    db = _FakeDB(owner=None)
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "claude_code"


@pytest.mark.asyncio
async def test_dispatch_passes_unknown_framework_through():
    """Forward-compat shift: unknown names are NOT silently rewritten
    by the dispatcher anymore — they propagate to ``get_agent_loop_driver``
    which raises ``ValueError`` so a typo surfaces. This is the v2
    behaviour deliberately chosen over silent fallback."""
    db = _FakeDB(owner_framework="future_framework_X")
    name = await _resolve_agent_framework_name("ag1", db)
    assert name == "future_framework_X"
    with pytest.raises(ValueError):
        get_agent_loop_driver(framework=name, working_path="/tmp")


@pytest.mark.asyncio
async def test_dispatch_falls_back_on_db_error():
    """Any DB error (connection lost, etc) → defensive claude_code."""
    name = await _resolve_agent_framework_name("ag1", _DeadDB())
    assert name == "claude_code"


# ----- DB query shape ----------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_reads_override_then_owner_default():
    """Pin the read order: agent_slots override first, then agents(owner) +
    the owner's user_slots agent row."""
    db = _FakeDB(owner_framework="codex_cli")
    await _resolve_agent_framework_name("ag1", db)
    assert db.calls == [
        ("agent_slots", {"agent_id": "ag1", "slot_name": "agent"}),
        ("agents", {"agent_id": "ag1"}),
        ("user_slots", {"user_id": "u1", "slot_name": "agent"}),
    ]
