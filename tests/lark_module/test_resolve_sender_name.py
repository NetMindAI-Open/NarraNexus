"""
@file_name: test_resolve_sender_name.py
@author: Bin Liang
@date: 2026-05-21
@description: Tests for LarkTrigger sender-name resolution identity.

Background: the bot tenant token lacks `contact:user.base:readonly`, so
`contact +get-user --as bot` returns only open_id/union_id (no name) for
every sender — the trigger fell back to "Unknown" for everyone and the
agent then guessed names from its roster (e.g. calling "kz" "Zehua").

Fix: resolve names via the per-agent OWNER user token (`--as user`),
which each lark-configured agent already holds in its isolated HOME
(populated by the three-click auth flow). Agents that never completed
Click 3 (no user token) intentionally stay "Unknown" rather than burning
a doomed subprocess.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient
from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


def _cred(user_oauth: bool):
    """Minimal LarkCredential stand-in carrying just what resolve needs."""
    return SimpleNamespace(
        agent_id="agent_test",
        profile_name="agent_test_profile",
        user_oauth_ok=lambda: user_oauth,
    )


@pytest.mark.asyncio
async def test_resolve_uses_user_identity_when_oauth_complete():
    trigger = LarkTrigger()
    trigger._cli = SimpleNamespace(
        get_user=AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "ok": True,
                    "identity": "user",
                    "data": {"user": {"name": "kz"}},
                },
            }
        )
    )

    name = await trigger.resolve_sender_name("ou_kz", _cred(user_oauth=True))

    assert name == "kz"
    trigger._cli.get_user.assert_awaited_once()
    kwargs = trigger._cli.get_user.await_args.kwargs
    assert kwargs.get("identity") == "user"


@pytest.mark.asyncio
async def test_resolve_skips_cli_when_no_user_token():
    trigger = LarkTrigger()
    trigger._cli = SimpleNamespace(get_user=AsyncMock())

    name = await trigger.resolve_sender_name("ou_kz", _cred(user_oauth=False))

    assert name == "Unknown"
    trigger._cli.get_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_returns_unknown_on_cli_failure():
    trigger = LarkTrigger()
    trigger._cli = SimpleNamespace(
        get_user=AsyncMock(return_value={"success": False, "error": "boom"})
    )

    name = await trigger.resolve_sender_name("ou_kz", _cred(user_oauth=True))

    assert name == "Unknown"


@pytest.mark.asyncio
async def test_get_user_builds_as_user_args():
    client = LarkCLIClient()
    client._run_with_agent_id = AsyncMock(return_value={"success": True, "data": {}})

    await client.get_user("agent_test", user_id="ou_kz")

    args = client._run_with_agent_id.await_args.args[0]
    assert args[:2] == ["contact", "+get-user"]
    assert "--as" in args and args[args.index("--as") + 1] == "user"
    assert "--user-id" in args and args[args.index("--user-id") + 1] == "ou_kz"
