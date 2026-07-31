"""
@file_name: test_login_mint_args.py
@date: 2026-07-29
@description: Guards the `auth login` invocation shared by Click 2 and
Click 3 of the three-click binding flow.

Two failure modes this pins down, both of which are silent in production:

1. **Click 3 narrower than Click 2.** The args used to be duplicated as
   two literals in `_advance_start` / `_advance_admin_approved`. The
   token's scope is whatever CLICK 3 requested, so editing only the
   Click 2 literal widens the admin approval request and then throws the
   extra grants away at mint time — no error anywhere.

2. **Silent loss of the extra scope block.** `--recommend` filters to
   lark-cli's auto-approve set; everything in `_EXTRA_LOGIN_SCOPES` rides
   on the explicit `--scope` argument. Dropping that argument degrades
   agents to the auto-approve subset with no failing call to point at.

Note the CLI floor this encodes: `--scope` only combines additively with
`--domain`/`--recommend` on lark-cli >= 1.0.31. Older CLIs reject the
combination outright, which is why the pinned desktop bundle version
matters (铁律 #7).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module import _lark_mcp_tools as tools

from .test_lark_permission_advance import _make_cred, _seed, fake_db  # noqa: F401


def test_mint_args_carry_recommend_and_explicit_scopes():
    args = tools._LOGIN_MINT_ARGS
    assert "--recommend" in args
    assert args[args.index("--domain") + 1] == "all"
    assert "--no-wait" in args and "--json" in args

    # The extra scopes ride as ONE space-separated argument — lark-cli's
    # `--scope` takes a single string, not repeated flags.
    scope_arg = args[args.index("--scope") + 1]
    assert scope_arg.split(" ") == tools._EXTRA_LOGIN_SCOPES


def test_extra_scopes_are_wellformed_and_unique():
    extras = tools._EXTRA_LOGIN_SCOPES
    assert len(extras) == len(set(extras)), "duplicate scope in _EXTRA_LOGIN_SCOPES"
    for scope in extras:
        assert scope == scope.strip(), f"{scope!r} has surrounding whitespace"
        assert " " not in scope, f"{scope!r} would split into two scopes"
        assert ":" in scope, f"{scope!r} is not a scope identifier"


def test_bot_identity_scopes_are_not_requested_via_auth_login():
    """`auth login` only ever grants USER identity.

    `im:message.group_msg` ("获取群组中所有消息", flagged sensitive by Lark)
    decides whether the bot receives every group message or only
    @-mentions. It is a bot-identity scope: enabled in the developer
    console, shipped by publishing a new app version, and reviewed by the
    tenant admin. Requesting it here would silently never be granted and
    would mislead whoever reads this list into thinking it is covered.
    """
    for bot_only in (
        "im:message.group_msg",
        "im:message:send_as_bot",
        "im:resource",
    ):
        assert bot_only not in tools._EXTRA_LOGIN_SCOPES

    # The user-identity twin IS the read path we rely on, and it already
    # arrives via `--recommend` — so it must not be duplicated here.
    assert "im:message.group_msg:get_as_user" not in tools._EXTRA_LOGIN_SCOPES


def test_impersonation_scope_stays_out():
    """The agent speaks as the bot; never as the owner."""
    assert "im:message.send_as_user" not in tools._EXTRA_LOGIN_SCOPES


@pytest.mark.asyncio
async def test_click2_and_click3_mint_identical_args(fake_db, monkeypatch):  # noqa: F811
    """The regression that motivated extracting `_LOGIN_MINT_ARGS`."""
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
    await tools._advance_start("agent_test", cred)
    click2_args = mock_run.call_args[0][0]

    cred = await LarkCredentialManager(fake_db).get_credential("agent_test")
    mock_run.reset_mock()
    mock_run.return_value = {
        "success": True,
        "data": {
            "verification_url": "https://lark.example/click3",
            "device_code": "DC_CLICK3",
        },
    }
    await tools._advance_admin_approved("agent_test", cred)
    click3_args = mock_run.call_args[0][0]

    assert click2_args == click3_args, (
        "Click 3 must request exactly what Click 2 got approved — a narrower "
        "Click 3 silently discards the extra grants."
    )


def test_every_auth_login_mint_site_uses_the_shared_constant():
    """No third call site may re-inline the args.

    A stale literal survived the first extraction pass in the expired-link
    re-mint branch of `_advance_user_authorized`; it would have silently
    downgraded every user whose Click 3 link timed out. Scan the source so
    a fourth site cannot reintroduce it.
    """
    import inspect
    import re

    source = inspect.getsource(tools)
    # Strip the constant's own definition — it is the ONE legitimate
    # `auth login` argv literal in the module.
    definition = re.search(
        r"_LOGIN_MINT_ARGS\s*=\s*\[[^\]]*\]", source, re.DOTALL
    )
    assert definition, "_LOGIN_MINT_ARGS definition not found"
    rest = source.replace(definition.group(0), "")

    offenders = [
        lit for lit in re.findall(r'\[\s*"auth"\s*,\s*"login"[^\]]*\]', rest)
        if "--recommend" in lit
    ]
    assert not offenders, (
        "inline `auth login` argv found — use _LOGIN_MINT_ARGS instead: "
        f"{offenders}"
    )
