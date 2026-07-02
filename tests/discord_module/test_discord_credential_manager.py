"""
@file_name: test_discord_credential_manager.py
@date: 2026-06-16
@description: Unit tests for DiscordCredentialManager.

Mirrors telegram_module/_telegram_credential_manager invariants, tailored
to Discord: GET /users/@me validation, numeric owner_user_id resolution,
bot-uniqueness on bot_user_id alone, base64 round-trip for the at-rest
token.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.discord_module import (
    _discord_credential_manager as cm_mod,
)
from xyz_agent_context.module.discord_module._discord_credential_manager import (
    DiscordCredentialManager,
    _decode_token,
    _encode_token,
)
from xyz_agent_context.module.discord_module.discord_sdk_client import DiscordSDKError


class _FakeDiscordClient:
    """Stand-in for DiscordSDKClient used by the manager during bind."""

    def __init__(
        self,
        bot_token: str,
        *,
        get_bot_raise: str | None = None,
        bot_user_id: str = "1001",
        bot_username: str = "acme",
        users: dict | None = None,
    ):
        self._bot_token = bot_token
        self._get_bot_raise = get_bot_raise
        self._bot_user_id = bot_user_id
        self._bot_username = bot_username
        self._users = users or {}

    async def get_bot_user(self) -> dict:
        if self._get_bot_raise:
            raise DiscordSDKError(self._get_bot_raise, "get_bot_user failed")
        return {"id": self._bot_user_id, "username": self._bot_username}

    async def get_user(self, user_id: str) -> dict:
        return self._users.get(user_id, {})


def _patch_ok(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(
        cm_mod, "DiscordSDKClient", lambda token: _FakeDiscordClient(token, **kwargs)
    )


# ── Encoding round-trip ────────────────────────────────────────────────


def test_encode_decode_round_trip():
    raw = "MTA.secret.payload"
    encoded = _encode_token(raw)
    assert encoded != raw
    assert _decode_token(encoded) == raw


def test_encode_empty_returns_empty():
    assert _encode_token("") == ""
    assert _decode_token("") == ""


# ── bind() ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_valid_inserts_row_and_returns_metadata(db_client, monkeypatch):
    _patch_ok(monkeypatch)
    mgr = DiscordCredentialManager(db_client)

    result = await mgr.bind("agent_a", "MTA.tok.real")

    assert result["success"] is True
    assert result["data"]["bot_user_id"] == "1001"
    assert result["data"]["bot_username"] == "acme"

    row = await db_client.get_one("channel_discord_credentials", {"agent_id": "agent_a"})
    assert row is not None
    assert row["bot_token_encoded"] != "MTA.tok.real"
    assert _decode_token(row["bot_token_encoded"]) == "MTA.tok.real"


@pytest.mark.asyncio
async def test_bind_rejects_empty_token(db_client, monkeypatch):
    _patch_ok(monkeypatch)
    mgr = DiscordCredentialManager(db_client)
    result = await mgr.bind("agent_a", "   ")
    assert result["success"] is False
    assert "required" in result["error"].lower()


@pytest.mark.asyncio
async def test_bind_rejects_non_numeric_owner_id(db_client, monkeypatch):
    _patch_ok(monkeypatch)
    mgr = DiscordCredentialManager(db_client)
    result = await mgr.bind("agent_a", "MTA.tok", owner_user_id="not-a-number")
    assert result["success"] is False
    assert "numeric" in result["error"].lower()
    assert await mgr.get("agent_a") is None


@pytest.mark.asyncio
async def test_bind_propagates_auth_failure(db_client, monkeypatch):
    monkeypatch.setattr(
        cm_mod,
        "DiscordSDKClient",
        lambda token: _FakeDiscordClient(token, get_bot_raise="unauthorized"),
    )
    mgr = DiscordCredentialManager(db_client)
    result = await mgr.bind("agent_a", "MTA.bad")
    assert result["success"] is False
    assert "Bot Token" in result["error"]
    assert await mgr.get("agent_a") is None


# ── Bot uniqueness across agents ───────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_rejects_same_bot_for_different_agent(db_client, monkeypatch):
    monkeypatch.setattr(
        cm_mod,
        "DiscordSDKClient",
        lambda token: _FakeDiscordClient(token, bot_user_id="9999", bot_username="shared"),
    )
    mgr = DiscordCredentialManager(db_client)

    first = await mgr.bind("agent_a", "MTA.toka")
    assert first["success"] is True

    second = await mgr.bind("agent_b", "MTA.tokb")
    assert second["success"] is False
    assert "already bound to another agent" in second["error"]
    assert "agent_a" in second["error"]
    assert await mgr.get("agent_b") is None


@pytest.mark.asyncio
async def test_bind_same_agent_is_rebind_not_conflict(db_client, monkeypatch):
    monkeypatch.setattr(
        cm_mod,
        "DiscordSDKClient",
        lambda token: _FakeDiscordClient(token, bot_user_id="42", bot_username="rebind"),
    )
    mgr = DiscordCredentialManager(db_client)

    assert (await mgr.bind("agent_a", "MTA.tok1"))["success"] is True
    assert (await mgr.bind("agent_a", "MTA.tok2"))["success"] is True

    rows = await db_client.get("channel_discord_credentials", {"agent_id": "agent_a"})
    assert len(rows) == 1
    assert _decode_token(rows[0]["bot_token_encoded"]) == "MTA.tok2"


# ── Owner resolution by numeric id ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_with_owner_id_resolves_name(db_client, monkeypatch):
    _patch_ok(
        monkeypatch,
        users={"555": {"username": "owner", "global_name": "Owner Name"}},
    )
    mgr = DiscordCredentialManager(db_client)

    res = await mgr.bind("agent_a", "MTA.tok", owner_user_id="555")
    assert res["success"] is True
    assert res["data"]["owner_user_id"] == "555"
    assert res["data"]["owner_name"] == "Owner Name"

    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.owner_user_id == "555"
    assert cred.owner_name == "Owner Name"


# ── get_public scrubs token ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_public_omits_token_fields(db_client, monkeypatch):
    _patch_ok(monkeypatch)
    mgr = DiscordCredentialManager(db_client)
    await mgr.bind("agent_a", "MTA.secret")

    public = await mgr.get_public("agent_a")
    assert public is not None
    assert "bot_token" not in public
    assert "bot_token_encoded" not in public
    assert public["bot_user_id"] == "1001"


# ── unbind / set_enabled / list_active ─────────────────────────────────


@pytest.mark.asyncio
async def test_unbind_removes_row(db_client, monkeypatch):
    _patch_ok(monkeypatch)
    mgr = DiscordCredentialManager(db_client)
    await mgr.bind("agent_a", "MTA.tok")

    assert await mgr.unbind("agent_a") is True
    assert await db_client.get_one("channel_discord_credentials", {"agent_id": "agent_a"}) is None


@pytest.mark.asyncio
async def test_unbind_returns_false_when_no_row(db_client):
    mgr = DiscordCredentialManager(db_client)
    assert await mgr.unbind("ghost") is False


@pytest.mark.asyncio
async def test_list_active_filters_disabled_rows(db_client, monkeypatch):
    def factory(token: str) -> _FakeDiscordClient:
        bot_id = "100" if "on" in token else "200"
        return _FakeDiscordClient(token, bot_user_id=bot_id, bot_username=f"bot_{bot_id}")

    monkeypatch.setattr(cm_mod, "DiscordSDKClient", factory)
    mgr = DiscordCredentialManager(db_client)

    await mgr.bind("agent_on", "MTA.tokon")
    await mgr.bind("agent_off", "MTA.tokoff")
    await mgr.set_enabled("agent_off", False)

    active = await mgr.list_active()
    agent_ids = {c.agent_id for c in active}
    assert "agent_on" in agent_ids
    assert "agent_off" not in agent_ids
