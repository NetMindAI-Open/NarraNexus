"""
@file_name: test_credential_activation.py
@author: NetMind.AI
@date: 2026-07-10
@description: Channel credential activation toggle — the flag-flip behind the
             "activate an imported (inactive) channel" feature.

Every IM channel exposes a manager method that flips the active flag without a
re-bind (Lark: set_is_active on is_active; the other four: set_enabled on
enabled). This is what the POST /api/<channel>/set-active endpoints call, and
what turns an imported-inactive bundle credential live so the trigger's
credential watcher claims the single connection slot. Lark's set_is_active is
new (added with this feature); the others pre-existed and are covered here for
parity/regression.

Uses the shared in-memory db_client fixture (tests/conftest.py).
"""

from __future__ import annotations

import pytest


async def _seed(db, table, agent_id, active_col, active_val):
    row = {"agent_id": agent_id, "bot_token_encoded": "dG9rZW4=", active_col: active_val}
    if table == "lark_credentials":
        row = {
            "agent_id": agent_id, "app_id": f"cli_{agent_id}", "app_secret_ref": "r",
            "brand": "lark", "profile_name": f"prof_{agent_id}", "is_active": active_val,
        }
    elif table == "channel_slack_credentials":
        row["app_token_encoded"] = "eGFwcC10b2tlbg=="
    await db.insert(table, row)


async def test_lark_set_is_active_flips_flag(db_client):
    """Lark's new set_is_active flips is_active and returns False when missing."""
    from xyz_agent_context.module.lark_module._lark_credential_manager import (
        LarkCredentialManager,
    )
    await _seed(db_client, "lark_credentials", "agent_lk", "is_active", 0)
    mgr = LarkCredentialManager(db_client)

    assert await mgr.set_is_active("agent_lk", True) is True
    cred = await mgr.get_credential("agent_lk")
    assert cred is not None and cred.is_active is True

    assert await mgr.set_is_active("agent_lk", False) is True
    cred = await mgr.get_credential("agent_lk")
    assert cred.is_active is False

    # No row → False (route surfaces "No bot bound").
    assert await mgr.set_is_active("agent_missing", True) is False


@pytest.mark.parametrize("channel", ["slack", "telegram", "wechat", "discord"])
async def test_set_enabled_flips_flag(db_client, channel):
    """The four `enabled`-column channels flip via set_enabled (parity)."""
    import importlib

    table = f"channel_{channel}_credentials"
    mgr_mod = importlib.import_module(
        f"xyz_agent_context.module.{channel}_module._{channel}_credential_manager"
    )
    mgr_cls = next(
        getattr(mgr_mod, n) for n in dir(mgr_mod) if n.endswith("CredentialManager")
    )
    await _seed(db_client, table, f"agent_{channel}", "enabled", 0)
    mgr = mgr_cls(db_client)

    assert await mgr.set_enabled(f"agent_{channel}", True) is True
    row = await db_client.get_one(table, {"agent_id": f"agent_{channel}"})
    assert row["enabled"] in (1, True)

    assert await mgr.set_enabled("agent_missing", True) is False
