"""
@file_name: test_wechat_credential_manager.py
@author:
@date: 2026-06-24
@description: DB-backed tests for ``WeChatCredentialManager``.

Exercises the full credential lifecycle against an in-memory SQLite
(the ``db_client`` fixture migrates ``channel_wechat_credentials`` from
schema_registry), with emphasis on the two non-obvious behaviours:

  1. ``get_public`` must NEVER leak the token; ``get`` returns it decoded.
  2. ``claim_owner`` is a compare-and-set on an empty ``owner_wx_id`` —
     only the first DM wins. This pins that ``db.update`` reports an
     affected-row count (the CAS is a no-op signal otherwise).
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
    WeChatCredentialManager,
)

pytestmark = pytest.mark.asyncio


async def test_bind_upserts_and_get_public_hides_token(db_client):
    mgr = WeChatCredentialManager(db_client)

    res = await mgr.bind("agent_a", "ilink-token-xyz", "https://gw.example", "user_1")
    assert res["success"] is True

    # get() returns the decoded token for caller-side use.
    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.bot_token == "ilink-token-xyz"
    assert cred.base_url == "https://gw.example"
    assert cred.owner_user_id == "user_1"
    assert cred.owner_wx_id == ""  # opaque until first DM

    # get_public() is the API/log-safe view — no token under any key.
    public = await mgr.get_public("agent_a")
    assert public is not None
    assert "ilink-token-xyz" not in str(public)
    assert "bot_token" not in public
    assert public["agent_id"] == "agent_a"
    assert public["enabled"] is True


async def test_bind_rejects_empty_token(db_client):
    mgr = WeChatCredentialManager(db_client)
    res = await mgr.bind("agent_a", "   ", "", "user_1")
    assert res["success"] is False
    assert await mgr.get("agent_a") is None


async def test_bind_is_idempotent_upsert(db_client):
    mgr = WeChatCredentialManager(db_client)
    await mgr.bind("agent_a", "token-1", "", "user_1")
    await mgr.bind("agent_a", "token-2", "https://gw2", "user_1")

    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.bot_token == "token-2"
    assert cred.base_url == "https://gw2"
    # Still exactly one active row for the agent.
    assert len(await mgr.list_active()) == 1


async def test_claim_owner_is_first_dm_wins(db_client):
    mgr = WeChatCredentialManager(db_client)
    await mgr.bind("agent_a", "token-1", "", "user_1")

    # First DM claims ownership.
    assert await mgr.claim_owner("agent_a", "wxid_owner") is True
    cred = await mgr.get("agent_a")
    assert cred is not None and cred.owner_wx_id == "wxid_owner"

    # A later DM from anyone else cannot re-claim (CAS on empty owner_wx_id).
    assert await mgr.claim_owner("agent_a", "wxid_stranger") is False
    cred = await mgr.get("agent_a")
    assert cred is not None and cred.owner_wx_id == "wxid_owner"


async def test_set_enabled_filters_list_active(db_client):
    mgr = WeChatCredentialManager(db_client)
    await mgr.bind("agent_a", "token-1", "", "user_1")
    await mgr.bind("agent_b", "token-2", "", "user_2")
    assert len(await mgr.list_active()) == 2

    assert await mgr.set_enabled("agent_a", False) is True
    active = await mgr.list_active()
    assert [c.agent_id for c in active] == ["agent_b"]


async def test_unbind_removes_row(db_client):
    mgr = WeChatCredentialManager(db_client)
    await mgr.bind("agent_a", "token-1", "", "user_1")

    assert await mgr.unbind("agent_a") is True
    assert await mgr.get("agent_a") is None
    # Unbinding a missing agent is a falsey no-op.
    assert await mgr.unbind("agent_a") is False
