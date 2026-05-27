"""
Test the Lark scope validator — making sure we correctly detect
required-vs-optional scope gaps and never punish the user when our
tooling itself fails.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.module.lark_module._lark_scope_validator import (
    ScopeCheckResult,
    REQUIRED_BOT_SCOPES,
    REQUIRED_USER_SCOPES,
    OPTIONAL_SCOPES,
    check_app_scopes,
    format_scope_failure_message,
    format_scope_warning_message,
    get_scope_policy,
)


def _make_cli(success: bool = True, data: dict | None = None, error: str = ""):
    cli = MagicMock()
    cli._run_with_agent_id = AsyncMock(
        return_value={"success": success, "data": data or {}, "error": error}
    )
    return cli


@pytest.mark.asyncio
async def test_check_all_scopes_present_no_warnings_no_blocking():
    # Provide BOTH required + optional → no warnings, no blocking
    cli = _make_cli(
        data={
            "bot_scopes": list(REQUIRED_BOT_SCOPES | OPTIONAL_SCOPES),
            "user_scopes": list(REQUIRED_USER_SCOPES),
        }
    )
    res = await check_app_scopes(cli, "agent_x")
    assert res.check_ran
    assert not res.is_blocking
    assert not res.has_warnings
    assert REQUIRED_BOT_SCOPES.issubset(set(res.granted_bot_scopes))


@pytest.mark.asyncio
async def test_check_missing_required_is_blocking():
    cli = _make_cli(
        data={
            "bot_scopes": ["im:message"],  # only one of several required
            "user_scopes": [],
        }
    )
    res = await check_app_scopes(cli, "agent_x")
    assert res.check_ran
    assert res.is_blocking
    assert "im:message:send_as_bot" in res.missing_required
    assert "contact:user.base:readonly" in res.missing_required


@pytest.mark.asyncio
async def test_check_required_full_but_optional_missing_warns_only():
    cli = _make_cli(
        data={
            "bot_scopes": list(REQUIRED_BOT_SCOPES),
            "user_scopes": list(REQUIRED_USER_SCOPES),
        }
    )
    res = await check_app_scopes(cli, "agent_x")
    assert not res.is_blocking
    assert res.has_warnings  # optional scopes all missing
    assert set(res.missing_optional) == OPTIONAL_SCOPES


@pytest.mark.asyncio
async def test_check_tool_failure_returns_check_ran_false():
    cli = _make_cli(success=False, error="lark-cli auth scopes timed out")
    res = await check_app_scopes(cli, "agent_x")
    assert not res.check_ran
    assert not res.is_blocking  # fail-open: don't punish user for our problem
    assert "timed out" in res.error


@pytest.mark.asyncio
async def test_check_handles_alternative_json_shape_flat_with_token_types():
    # Some lark-cli versions emit a flat `scopes` list with `token_types` tags
    cli = _make_cli(
        data={
            "scopes": [
                {"scope": "im:message", "token_types": ["bot"]},
                {"scope": "im:message:send_as_bot", "token_types": ["bot"]},
                {"scope": "im:resource", "token_types": ["bot"]},
                {"scope": "im:chat", "token_types": ["bot"]},
                {"scope": "im:chat:readonly", "token_types": ["bot"]},
                {"scope": "contact:user.base:readonly", "token_types": ["user"]},
            ]
        }
    )
    res = await check_app_scopes(cli, "agent_x")
    assert res.check_ran
    assert not res.is_blocking
    assert "im:message" in res.granted_bot_scopes
    assert "contact:user.base:readonly" in res.granted_user_scopes


@pytest.mark.asyncio
async def test_check_handles_camelcase_keys():
    cli = _make_cli(
        data={
            "botScopes": list(REQUIRED_BOT_SCOPES),
            "userScopes": list(REQUIRED_USER_SCOPES),
        }
    )
    res = await check_app_scopes(cli, "agent_x")
    assert res.check_ran
    assert not res.is_blocking


def test_format_scope_failure_message_includes_console_url_feishu():
    res = ScopeCheckResult(
        missing_required=["im:message", "contact:user.base:readonly"],
        check_ran=True,
    )
    msg = format_scope_failure_message(res, brand="feishu", app_id="cli_test")
    assert "open.feishu.cn" in msg
    assert "im:message" in msg
    assert "publish" in msg.lower()


def test_format_scope_failure_message_includes_console_url_lark():
    res = ScopeCheckResult(missing_required=["im:resource"], check_ran=True)
    msg = format_scope_failure_message(res, brand="lark", app_id="cli_test")
    assert "open.larksuite.com" in msg
    assert "im:resource" in msg


def test_format_scope_warning_message_lists_missing_optional():
    res = ScopeCheckResult(
        missing_optional=["docs:document", "calendar:calendar"],
        check_ran=True,
    )
    msg = format_scope_warning_message(res)
    assert "docs:document" in msg
    assert "calendar:calendar" in msg
    assert "optional" in msg.lower()


def test_get_scope_policy_returns_all_three_categories():
    p = get_scope_policy()
    assert set(p.keys()) == {
        "required_bot_scopes", "required_user_scopes", "optional_scopes"
    }
    assert "im:message" in p["required_bot_scopes"]
    assert "contact:user.base:readonly" in p["required_user_scopes"]
    assert "docs:document" in p["optional_scopes"]


def test_scope_check_result_to_dict_roundtrip():
    res = ScopeCheckResult(
        missing_required=["a"],
        missing_optional=["b", "c"],
        granted_bot_scopes=["x"],
        granted_user_scopes=["y"],
        check_ran=True,
    )
    d = res.to_dict()
    assert set(d.keys()) >= {
        "missing_required", "missing_optional", "granted_bot_scopes",
        "granted_user_scopes", "check_ran", "error",
    }
