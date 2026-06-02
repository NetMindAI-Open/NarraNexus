"""
@file_name: test_lark_unbind.py
@date: 2026-05-22
@description: Tests for ``_lark_service.do_unbind`` and its wiring
into the ``lark_unbind`` MCP tool.

Background: the agent could not unbind Lark via natural language —
the MCP toolset exposed ``lark_bind`` / ``lark_setup`` / ``lark_status``
but no symmetrical ``lark_unbind``. Agents replied "Lark module
currently has no unbind tool, I cannot disconnect directly" when the
user asked to disconnect. Existing unbind logic lived inline in
``backend/routes/lark.py`` so it wasn't reachable from MCP without
duplication.

The fix:
  - Extract route cleanup into ``_lark_service.do_unbind``.
  - HTTP route + ``lark_unbind`` MCP tool both call it.
  - Prompt mentions the tool so the agent surfaces it on
    "解绑 / unbind / disconnect" intents.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from xyz_agent_context.module.lark_module._lark_service import do_unbind


# ── Fakes ───────────────────────────────────────────────────────────


class _FakeDB:
    """Minimal AsyncDatabaseClient stand-in for bus channel cleanup."""

    def __init__(self) -> None:
        # table_name -> list[row]
        self.rows: dict[str, list[dict[str, Any]]] = {
            "bus_channel_members": [],
            "bus_messages": [],
            "bus_channels": [],
        }
        # Record every mutation so tests can assert exact sequence
        self.deletes: list[tuple[str, dict[str, Any]]] = []

    async def get(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for row in self.rows.get(table, []):
            if all(row.get(k) == v for k, v in filters.items()):
                out.append(row)
        return out

    async def delete(self, table: str, filters: dict[str, Any]) -> None:
        self.deletes.append((table, dict(filters)))
        self.rows[table] = [
            row
            for row in self.rows.get(table, [])
            if not all(row.get(k) == v for k, v in filters.items())
        ]


class _FakeCredentialManager:
    """Minimal LarkCredentialManager stand-in.

    Only mocks the two methods do_unbind calls: ``get_credential`` and
    ``delete_credential``."""

    def __init__(self, has_credential: bool = True) -> None:
        self._has = has_credential
        self.delete_calls: list[str] = []

    async def get_credential(self, agent_id: str):
        if self._has:
            return SimpleNamespace(agent_id=agent_id, app_id="cli_test")
        return None

    async def delete_credential(self, agent_id: str) -> None:
        self.delete_calls.append(agent_id)


# ── do_unbind: happy path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_do_unbind_removes_credential_and_returns_unbound():
    mgr = _FakeCredentialManager(has_credential=True)
    db = _FakeDB()

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove"
    ) as profile_remove, patch(
        "xyz_agent_context.module.lark_module._lark_service.cleanup_workspace"
        if False  # cleanup_workspace is imported inside do_unbind, not at module level
        else "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace"
    ) as cleanup_ws:
        result = await do_unbind(mgr, "agent_1", db)

    assert result == {"success": True, "data": {"unbound": True}}
    assert mgr.delete_calls == ["agent_1"]
    profile_remove.assert_called_once_with("agent_1")
    cleanup_ws.assert_called_once_with("agent_1")


@pytest.mark.asyncio
async def test_do_unbind_returns_no_credential_when_nothing_bound():
    """No-credential case must NOT touch CLI / workspace / DB."""
    mgr = _FakeCredentialManager(has_credential=False)
    db = _FakeDB()

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove"
    ) as profile_remove, patch(
        "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace"
    ) as cleanup_ws:
        result = await do_unbind(mgr, "agent_2", db)

    assert result["success"] is False
    assert result["error"] == "no_credential"
    assert "no lark bot bound" in result["message"].lower()
    assert mgr.delete_calls == []
    profile_remove.assert_not_called()
    cleanup_ws.assert_not_called()


# ── do_unbind: cleanup robustness ───────────────────────────────────


@pytest.mark.asyncio
async def test_do_unbind_continues_when_profile_remove_raises():
    """Best-effort CLI cleanup must not abort the unbind. The DB row
    is the source of truth — a stuck row after failed cleanup is
    worse than a leaked keychain entry."""
    mgr = _FakeCredentialManager(has_credential=True)
    db = _FakeDB()

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove",
        side_effect=RuntimeError("keychain locked"),
    ), patch(
        "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace"
    ):
        result = await do_unbind(mgr, "agent_3", db)

    assert result["success"] is True
    assert mgr.delete_calls == ["agent_3"]


@pytest.mark.asyncio
async def test_do_unbind_continues_when_workspace_cleanup_raises():
    mgr = _FakeCredentialManager(has_credential=True)
    db = _FakeDB()

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove"
    ), patch(
        "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace",
        side_effect=OSError("permission denied"),
    ):
        result = await do_unbind(mgr, "agent_4", db)

    assert result["success"] is True
    assert mgr.delete_calls == ["agent_4"]


# ── do_unbind: bus channel reap ─────────────────────────────────────


@pytest.mark.asyncio
async def test_do_unbind_reaps_only_lark_channels():
    """Removes the agent from every `lark_*` channel; non-Lark
    channels (slack_*, telegram_*) must be untouched."""
    mgr = _FakeCredentialManager(has_credential=True)
    db = _FakeDB()
    db.rows["bus_channel_members"] = [
        {"channel_id": "lark_abc", "agent_id": "agent_5"},
        {"channel_id": "lark_def", "agent_id": "agent_5"},
        {"channel_id": "slack_xyz", "agent_id": "agent_5"},
        {"channel_id": "telegram_123", "agent_id": "agent_5"},
    ]

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove"
    ), patch(
        "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace"
    ):
        await do_unbind(mgr, "agent_5", db)

    deleted_channels = {
        f.get("channel_id")
        for table, f in db.deletes
        if table == "bus_channel_members" and "channel_id" in f
    }
    assert "lark_abc" in deleted_channels
    assert "lark_def" in deleted_channels
    assert "slack_xyz" not in deleted_channels
    assert "telegram_123" not in deleted_channels


@pytest.mark.asyncio
async def test_do_unbind_reaps_empty_channel_messages_and_metadata():
    """When the last member is removed from a ``lark_*`` channel, also
    drop ``bus_messages`` and ``bus_channels`` for that channel.
    Stale messages after re-bind would break dedup."""
    mgr = _FakeCredentialManager(has_credential=True)
    db = _FakeDB()
    db.rows["bus_channel_members"] = [
        {"channel_id": "lark_solo", "agent_id": "agent_6"},
    ]

    with patch(
        "xyz_agent_context.module.lark_module._lark_service._cli.profile_remove"
    ), patch(
        "xyz_agent_context.module.lark_module._lark_workspace.cleanup_workspace"
    ):
        await do_unbind(mgr, "agent_6", db)

    tables_deleted = [t for t, _ in db.deletes]
    assert "bus_messages" in tables_deleted
    assert "bus_channels" in tables_deleted


# ── Prompt: the agent must see the tool ─────────────────────────────


def test_no_bot_prompt_mentions_lark_unbind():
    """When no bot is bound, asking the agent to "unbind" should
    still produce a deterministic action — we route it through
    ``lark_unbind`` which returns ``no_credential``."""
    from xyz_agent_context.module.lark_module.lark_module import _NO_BOT_INSTRUCTION

    assert "lark_unbind" in _NO_BOT_INSTRUCTION


def test_lifecycle_line_mentions_unbind_tool():
    """The operational prompt (bot bound) must surface the unbind
    tool name and the destructive-action warning."""
    from xyz_agent_context.module.lark_module.lark_module import _LIFECYCLE_LINE

    assert "mcp__lark_module__lark_unbind" in _LIFECYCLE_LINE
    assert "destructive" in _LIFECYCLE_LINE.lower()
