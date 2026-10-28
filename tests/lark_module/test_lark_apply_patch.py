"""
@file_name: test_lark_apply_patch.py
@author: Bin Liang
@date: 2026-08-19
@description: LarkCredentialManager.apply_patch / save_raw over the generic credential store: nested permission_state merges, only the patched fields change, unknown fields and missing credentials fail loud, save_raw pins the path's agent_id.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.channel.credential_store import GenericCredentialStore
from narranexus_plugins.lark_module._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
    _encode_secret,
)


def _cred(**over) -> LarkCredential:
    base = dict(
        agent_id="agent_x", app_id="cli_x", app_secret_ref="ref", app_secret_encoded=_encode_secret("s3cret"),
        brand="feishu", profile_name="agent_agent_x", workspace_path="/ws", bot_name="Bot", bot_open_id="ou_b",
        owner_open_id="ou_o", owner_name="Al", auth_status="bot_ready", is_active=True,
        permission_state={"admin_request_url": "u1", "admin_approved_at": "t0"},
    )
    base.update(over)
    return LarkCredential(**base)


async def _seeded(db_client) -> LarkCredentialManager:
    mgr = LarkCredentialManager(db_client)
    await mgr.save_credential(_cred())
    return mgr


@pytest.mark.asyncio
async def test_apply_patch_merges_permission_state_into_the_existing_blob(db_client):
    mgr = await _seeded(db_client)
    await mgr.apply_patch("agent_x", {"permission_state": {"user_authz_url": "u3"}})
    cred = await mgr.get_credential("agent_x")
    assert cred.permission_state == {"admin_request_url": "u1", "admin_approved_at": "t0", "user_authz_url": "u3"}
    # a disjoint field is untouched by the merge
    assert cred.auth_status == "bot_ready" and cred.get_app_secret() == "s3cret"


@pytest.mark.asyncio
async def test_apply_patch_writes_only_the_patched_fields(db_client):
    mgr = await _seeded(db_client)
    before = await GenericCredentialStore(db_client).get("lark", "agent_x")
    await mgr.apply_patch("agent_x", {"app_id": "cli_new", "is_active": False})
    after = await GenericCredentialStore(db_client).get("lark", "agent_x")
    cred = await mgr.get_credential("agent_x")
    assert cred.app_id == "cli_new" and cred.is_active is False and after.enabled is False
    assert after.version == before.version + 1
    # everything else survived the patch
    assert cred.auth_status == "bot_ready" and cred.workspace_path == "/ws" and cred.bot_name == "Bot"


@pytest.mark.asyncio
async def test_concurrent_disjoint_writers_do_not_clobber_each_other(db_client):
    """The trigger's status write and the panel's permission patch race on one row."""
    mgr = await _seeded(db_client)
    await asyncio.gather(
        mgr.update_auth_status("agent_x", "user_logged_in"),
        mgr.apply_patch("agent_x", {"permission_state": {"user_authz_url": "u3"}}),
    )
    cred = await mgr.get_credential("agent_x")
    assert cred.auth_status == "user_logged_in" and cred.permission_state.get("user_authz_url") == "u3"


@pytest.mark.asyncio
async def test_apply_patch_rejects_unknown_field(db_client):
    mgr = await _seeded(db_client)
    with pytest.raises(ValueError, match="unknown lark credential field"):
        await mgr.apply_patch("agent_x", {"not_a_real_column": "x"})


@pytest.mark.asyncio
async def test_apply_patch_on_missing_credential_raises_clearly(db_client):
    mgr = LarkCredentialManager(db_client)
    with pytest.raises(ValueError, match="no Lark credential"):
        await mgr.apply_patch("nobody", {"auth_status": "x"})


@pytest.mark.asyncio
async def test_save_raw_pins_agent_id_from_the_path(db_client):
    mgr = await _seeded(db_client)
    # a body that tries to retarget another agent must NOT win — path agent_id pins
    await mgr.save_raw("agent_x", {**_cred().to_raw_dict(), "agent_id": "attacker", "app_id": "cli_new"})
    assert await mgr.get_credential("attacker") is None
    assert (await mgr.get_credential("agent_x")).app_id == "cli_new"
