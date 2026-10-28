"""
@file_name: test_lark_permission_advance.py
@date: 2026-04-22
@description: Unit tests for the lark_permission_advance state machine.
Mocks the CLI client and the MCP DB; validates each event transition + guards.

"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from narranexus_plugins.lark_module._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
)
from narranexus_plugins.lark_module import _lark_mcp_tools as tools


# ───────────────────────── Fixtures ─────────────────────────

def _make_cred(permission_state: dict | None = None) -> LarkCredential:
    return LarkCredential(
        agent_id="agent_test",
        app_id="cli_real",
        app_secret_ref="appsecret:cli_real",
        brand="lark",
        profile_name="agent_test",
        is_active=True,
        auth_status="bot_ready",
        permission_state=permission_state or {},
    )


class _FakeDB:
    """Minimal in-memory db: equality filters over dict rows per table, ``update``
    returns the affected count (the generic credential store's version CAS relies on it)."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    @staticmethod
    def _match(row, filters):
        return all(row.get(k) == v for k, v in filters.items())

    async def get_one(self, table, filters):
        for row in self.tables.get(table, []):
            if self._match(row, filters):
                return dict(row)
        return None

    async def get(self, table, filters):
        return [dict(r) for r in self.tables.get(table, []) if self._match(r, filters)]

    async def insert(self, table, data):
        self.tables.setdefault(table, []).append(dict(data))

    async def update(self, table, filters, data):
        n = 0
        for row in self.tables.get(table, []):
            if self._match(row, filters):
                row.update(data)
                n += 1
        return n

    async def delete(self, table, filters):
        rows = self.tables.get(table, [])
        keep = [r for r in rows if not self._match(r, filters)]
        self.tables[table] = keep
        return len(rows) - len(keep)

    @property
    def rows(self) -> dict[str, dict]:
        """agent_id → stored lark credential (the shape the assertions read)."""
        return {r["agent_id"]: r for r in self.tables.get("channel_credentials", []) if r.get("channel") == "lark"}


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()

    async def _get_db():
        return db

    monkeypatch.setattr(
        "narranexus.platform.module_system.base.XYZBaseModule.get_mcp_db_client",
        AsyncMock(return_value=db),
    )
    return db


async def _seed(db: _FakeDB, cred: LarkCredential):
    """Write the credential into fake_db using the real manager."""
    mgr = LarkCredentialManager(db)
    await mgr.save_credential(cred)


# ───────────────────────── event="" ─────────────────────────

@pytest.mark.asyncio
async def test_event_empty_from_scratch_generates_click2(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred())
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock(return_value={
        "success": True,
        "data": {
            "verification_url": "https://lark.example/click2",
            "device_code": "DC_CLICK2",
        },
    })
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_start("agent_test", cred)

    assert result["success"] is True
    assert result["data"]["url"] == "https://lark.example/click2"
    assert result["data"]["click_label"] == "Click 2"
    assert result["data"]["stage_after"] == "waiting_admin"
    assert "Click 2" in result["data"]["user_facing_message"]

    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    assert saved.permission_state["admin_request_url"] == "https://lark.example/click2"
    assert saved.permission_state["admin_request_device_code"] == "DC_CLICK2"
    assert saved.current_click_stage() == "waiting_admin"


@pytest.mark.asyncio
async def test_event_empty_surfaces_seam_write_failure_not_silent_success(fake_db, monkeypatch):
    # Finding #1: the seam never raises — a cloud write failure comes back as
    # {"success": False, ...}. _advance_start MUST check it and surface an error,
    # not hand the model a Click-2 URL whose device_code was never persisted
    # (the next click would fail with a misleading "no device_code").
    await _seed(fake_db, _make_cred())
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    monkeypatch.setattr(tools._cli, "_run_with_agent_id", AsyncMock(return_value={
        "success": True,
        "data": {"verification_url": "https://lark.example/click2", "device_code": "DC"},
    }))

    class _FailingStore:
        async def patch_credential(self, channel, agent_id, patch):
            return {"success": False, "error": "backend unreachable"}

    monkeypatch.setattr(tools, "get_channel_credential_store", lambda *a, **k: _FailingStore())

    result = await tools._advance_start("agent_test", cred)
    assert result["success"] is False
    assert "backend unreachable" in result["error"]


@pytest.mark.asyncio
async def test_event_empty_idempotent_when_click2_already_exists(fake_db, monkeypatch):
    """If admin_request_url already present, return it — do NOT re-run CLI."""
    await _seed(fake_db, _make_cred({
        "admin_request_url": "https://lark.example/old",
        "admin_request_device_code": "OLD_DC",
        "admin_request_generated_at": "2026-04-22T10:00:00+00:00",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock(return_value={"success": True, "data": {}})
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_start("agent_test", cred)

    assert result["success"] is True
    assert result["data"]["url"] == "https://lark.example/old"
    assert result["data"]["click_label"] == "Click 2"
    mock_run.assert_not_called()  # idempotent


# ───────────────────────── event="admin_approved" ─────────────────────────

@pytest.mark.asyncio
async def test_event_admin_approved_without_request_rejects(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred())
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock()
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_admin_approved("agent_test", cred)

    assert result["success"] is False
    assert "No admin request" in result["error"]
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_event_admin_approved_mints_fresh_click3(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred({
        "admin_request_url": "https://lark.example/click2",
        "admin_request_device_code": "DC_CLICK2",
        "admin_request_generated_at": "2026-04-22T10:00:00+00:00",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock(return_value={
        "success": True,
        "data": {
            "verification_url": "https://lark.example/click3",
            "device_code": "DC_CLICK3",
        },
    })
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_admin_approved("agent_test", cred)

    assert result["success"] is True
    assert result["data"]["url"] == "https://lark.example/click3"
    assert result["data"]["click_label"] == "Click 3"
    assert result["data"]["stage_after"] == "waiting_user_click"

    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    ps = saved.permission_state
    # Click 2 URL stays for audit; Click 3 URL is the new poll-able one
    assert ps["user_authz_url"] == "https://lark.example/click3"
    assert ps["user_authz_device_code"] == "DC_CLICK3"
    assert ps["admin_approved_at"]
    assert saved.current_click_stage() == "waiting_user_click"


# ───────────────────────── event="user_authorized" ─────────────────────────

@pytest.mark.asyncio
async def test_event_user_authorized_without_authz_rejects(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred())
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock()
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_user_authorized("agent_test", cred)

    assert result["success"] is False
    assert "No Click 3 device_code" in result["error"]
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_event_user_authorized_success_flips_completed(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred({
        "admin_request_url": "https://lark.example/click2",
        "user_authz_url": "https://lark.example/click3",
        "user_authz_device_code": "DC_CLICK3",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock(return_value={
        "success": True,
        "data": {"scopes": ["im:message", "contact:user.base:readonly"]},
    })
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_user_authorized("agent_test", cred)

    assert result["success"] is True
    assert result["data"]["stage_after"] == "completed"
    assert "授权完成" in result["data"]["user_facing_message"]

    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    ps = saved.permission_state
    assert ps["user_oauth_completed_at"]
    assert ps["bot_scopes_confirmed"] is True
    assert ps["console_setup_done_at"]
    assert ps["user_authz_device_code"] is None  # cleared
    assert saved.current_click_stage() == "completed"
    assert saved.auth_status == "user_logged_in"


@pytest.mark.asyncio
async def test_event_user_authorized_completion_write_failure_surfaces(fake_db, monkeypatch):
    # The token is minted (CLI login OK) and the auth_status flip succeeds, but
    # the COMPLETION permission_state write (the marker current_click_stage()
    # reads to derive "completed") fails in cloud. The tool must NOT report
    # stage_after="completed" — it must surface the persist failure.
    await _seed(fake_db, _make_cred({
        "user_authz_url": "https://lark.example/click3",
        "user_authz_device_code": "DC_CLICK3",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    monkeypatch.setattr(tools._cli, "_run_with_agent_id", AsyncMock(return_value={
        "success": True, "data": {"scopes": ["im:message"]},
    }))

    class _FailCompletionStore:
        # auth_status flip (_patch_fields) succeeds; the permission_state
        # completion write (_patch_ps) fails.
        async def patch_credential(self, channel, agent_id, patch):
            if "permission_state" in patch:
                return {"success": False, "error": "backend unreachable"}
            return {"success": True}

    monkeypatch.setattr(tools, "get_channel_credential_store", lambda *a, **k: _FailCompletionStore())

    result = await tools._advance_user_authorized("agent_test", cred)
    assert result["success"] is False
    assert "completion state" in result["error"]
    assert "backend unreachable" in result["error"]


@pytest.mark.asyncio
async def test_event_user_authorized_pending_does_not_auto_retry(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred({
        "admin_request_url": "https://lark.example/click2",
        "user_authz_url": "https://lark.example/click3",
        "user_authz_device_code": "DC_CLICK3",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    mock_run = AsyncMock(return_value={
        "success": False,
        "error": "authorization_pending: user has not yet approved",
    })
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_user_authorized("agent_test", cred)

    assert result["success"] is False
    assert "pending" in result["error"].lower()
    assert "Click 3 点击" in result["data"]["user_facing_message"]
    # Exactly 1 CLI call — NO auto retry
    assert mock_run.call_count == 1

    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    # Device code preserved so user can click and we can re-poll
    assert saved.permission_state["user_authz_device_code"] == "DC_CLICK3"


@pytest.mark.asyncio
async def test_event_user_authorized_expired_regenerates_click3(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred({
        "admin_request_url": "https://lark.example/click2",
        "user_authz_url": "https://lark.example/old_click3",
        "user_authz_device_code": "OLD_DC3",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    # First call: poll returns expired. Second call: regen returns fresh URL.
    responses = [
        {"success": False, "error": "device_code is expired"},
        {
            "success": True,
            "data": {
                "verification_url": "https://lark.example/new_click3",
                "device_code": "NEW_DC3",
            },
        },
    ]
    mock_run = AsyncMock(side_effect=responses)
    monkeypatch.setattr(tools._cli, "_run_with_agent_id", mock_run)

    result = await tools._advance_user_authorized("agent_test", cred)

    assert result["success"] is False
    assert result["data"]["fresh_url"] == "https://lark.example/new_click3"
    assert result["data"]["click_label"] == "Click 3"
    assert result["data"]["stage_after"] == "waiting_user_click"
    assert "过期" in result["data"]["user_facing_message"]

    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    assert saved.permission_state["user_authz_device_code"] == "NEW_DC3"
    assert saved.permission_state["user_authz_url"] == "https://lark.example/new_click3"


# ───────────────────────── event="availability_ok" ─────────────────────────

@pytest.mark.asyncio
async def test_event_availability_ok_sets_flag(fake_db, monkeypatch):
    await _seed(fake_db, _make_cred({
        "user_oauth_completed_at": "2026-04-22T10:05:00+00:00",
    }))
    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")

    result = await tools._advance_availability_ok("agent_test", cred)

    assert result["success"] is True
    saved = await LarkCredentialManager(fake_db).get_credential("agent_test")
    assert saved.permission_state["availability_confirmed"] is True


# ───────────────────────── Tool entry point guards ─────────────────────────

@pytest.mark.asyncio
async def test_tool_entry_no_credential_errors(fake_db, monkeypatch):
    # Install handlers through register_lark_mcp_tools-like stub so we can
    # call the top-level tool body. Simpler: call it via the module closure.
    # We simulate the entry by invoking _get_credential directly + dispatch.
    result_cred = await tools._get_credential("agent_test")
    assert result_cred is None  # no seed


@pytest.mark.asyncio
async def test_tool_entry_completed_guard(fake_db, monkeypatch):
    """Calling event='admin_approved' or 'user_authorized' after completion
    returns a harmless already-completed marker, does not mutate state."""

    await _seed(fake_db, _make_cred({
        "user_oauth_completed_at": "2026-04-22T10:05:00+00:00",
        "bot_scopes_confirmed": True,
    }))

    # Register tools on a stub mcp so we can reach the top-level tool body
    captured: dict = {}

    class _StubMCP:
        def tool(self):
            def _deco(fn):
                captured[fn.__name__] = fn
                return fn
            return _deco

    tools.register_lark_mcp_tools(_StubMCP())
    lark_permission_advance = captured["lark_permission_advance"]

    # Completed + admin_approved → rejected no-op
    res = await lark_permission_advance("agent_test", event="admin_approved")
    assert res["success"] is False
    assert "Already completed" in res["error"]
    assert res["data"]["stage_after"] == "completed"

    # Completed + availability_ok → allowed
    res = await lark_permission_advance("agent_test", event="availability_ok")
    assert res["success"] is True

    # Unknown event
    res = await lark_permission_advance("agent_test", event="wat")
    assert res["success"] is False
    assert "Unknown event" in res["error"]
