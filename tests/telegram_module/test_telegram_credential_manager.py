"""
@file_name: test_telegram_credential_manager.py
@date: 2026-05-09
@description: Unit tests for TelegramCredentialManager.

Why this file exists:
    Mirrors slack_module/_slack_credential_manager.py invariants but
    tailored to Telegram: token shape ``<digits>:<base64>``, ``getMe``
    validation, optional ``getChat("@handle")`` owner resolution,
    bot-uniqueness on ``bot_user_id`` alone (no team_id), and base64
    round-trip for at-rest token encoding.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.telegram_module import (
    _telegram_credential_manager as cm_mod,
)
from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredentialManager,
    _decode_token,
    _encode_token,
)
from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
    TelegramSDKError,
)


class _FakeTelegramClient:
    """Stand-in for TelegramSDKClient used by the manager during bind."""

    def __init__(
        self,
        bot_token: str,
        *,
        get_me_raise: str | None = None,
        bot_user_id: str = "1001",
        bot_username: str = "acme_bot",
        get_chat_lookup: dict | None = None,
    ):
        self._bot_token = bot_token
        self._get_me_raise = get_me_raise
        self._bot_user_id = bot_user_id
        self._bot_username = bot_username
        self._get_chat_lookup = get_chat_lookup or {}

    async def delete_webhook(self) -> bool:
        return True

    async def get_me(self) -> dict:
        if self._get_me_raise:
            raise TelegramSDKError(self._get_me_raise, "getMe failed")
        return {
            "id": int(self._bot_user_id),
            "username": self._bot_username,
            "first_name": "Acme",
        }

    async def get_chat(self, chat_id_or_handle):
        handle = str(chat_id_or_handle)
        if handle in self._get_chat_lookup:
            return self._get_chat_lookup[handle]
        raise TelegramSDKError("chat_not_found", "getChat failed")

    async def close(self) -> None:
        return None


def _patch_ok(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(token, **kwargs),
    )


# ── Encoding round-trip ────────────────────────────────────────────────


def test_encode_decode_round_trip():
    raw = "7981632450:AAH-secretpayload"
    encoded = _encode_token(raw)
    assert encoded != raw
    assert _decode_token(encoded) == raw


def test_encode_empty_returns_empty():
    assert _encode_token("") == ""
    assert _decode_token("") == ""


# ── bind() ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_valid_inserts_row_and_returns_metadata(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)

    result = await mgr.bind("agent_a", "7981632450:AAH-real-XXXXXXXXXXXXXXXX")

    assert result["success"] is True
    assert result["data"]["bot_user_id"] == "1001"
    assert result["data"]["bot_username"] == "acme_bot"

    row = await db_client.get_one(
        "channel_telegram_credentials", {"agent_id": "agent_a"}
    )
    assert row is not None
    assert row["bot_token_encoded"] != "7981632450:AAH-real-XXXXXXXXXXXXXXXX"
    assert _decode_token(row["bot_token_encoded"]) == "7981632450:AAH-real-XXXXXXXXXXXXXXXX"


@pytest.mark.asyncio
async def test_bind_rejects_invalid_token_prefix(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)

    result = await mgr.bind("agent_a", "no-colon-here")

    assert result["success"] is False
    assert ":" in result["error"]
    row = await db_client.get_one(
        "channel_telegram_credentials", {"agent_id": "agent_a"}
    )
    assert row is None


@pytest.mark.asyncio
async def test_bind_propagates_get_me_failure(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(token, get_me_raise="Unauthorized"),
    )
    mgr = TelegramCredentialManager(db_client)

    result = await mgr.bind("agent_a", "123456789:AAH-bad-token-XXXXXXXXXX")

    assert result["success"] is False
    # Friendly mapping (added 2026-05-12) — surface user-actionable text,
    # not the raw Telegram description. Old assertion was
    # ``"Unauthorized" in result["error"]``.
    assert "Bot Token" in result["error"] and "rejected" in result["error"].lower()
    row = await db_client.get_one(
        "channel_telegram_credentials", {"agent_id": "agent_a"}
    )
    assert row is None


# ── Bot uniqueness across agents ───────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_rejects_same_bot_for_different_agent(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """Same bot_user_id cannot be bound to two agents."""
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(
            token, bot_user_id="9999", bot_username="shared_bot"
        ),
    )
    mgr = TelegramCredentialManager(db_client)

    first = await mgr.bind("agent_a", "123456789:AAH-toka-XXXXXXXXXXXXXXXX")
    assert first["success"] is True

    second = await mgr.bind("agent_b", "123456790:AAH-tokb-XXXXXXXXXXXXXXXX")
    assert second["success"] is False
    assert "already bound to another agent" in second["error"]
    assert "agent_a" in second["error"]
    assert await mgr.get("agent_b") is None


@pytest.mark.asyncio
async def test_bind_same_bot_same_agent_is_rebind_not_conflict(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(
            token, bot_user_id="42", bot_username="rebind_bot"
        ),
    )
    mgr = TelegramCredentialManager(db_client)

    first = await mgr.bind("agent_a", "123456789:AAH-tok1-XXXXXXXXXXXXXXXX")
    assert first["success"] is True

    second = await mgr.bind("agent_a", "123456789:AAH-tok2-XXXXXXXXXXXXXXXX")
    assert second["success"] is True

    rows = await db_client.get(
        "channel_telegram_credentials", {"agent_id": "agent_a"}
    )
    assert len(rows) == 1
    assert _decode_token(rows[0]["bot_token_encoded"]) == "123456789:AAH-tok2-XXXXXXXXXXXXXXXX"


# ── Owner resolution via @username ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_with_owner_username_resolves_user_id(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(
            token,
            get_chat_lookup={
                "@bin_liang": {
                    "id": 555,
                    "first_name": "Bin",
                    "last_name": "Liang",
                }
            },
        ),
    )
    mgr = TelegramCredentialManager(db_client)

    res = await mgr.bind("agent_a", "123456789:AAH-tok-XXXXXXXXXXXXXXXXX", owner_username="@bin_liang")
    assert res["success"] is True
    assert res["data"]["owner_user_id"] == "555"
    assert res["data"]["owner_name"] == "Bin Liang"

    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.owner_username == "bin_liang"
    assert cred.owner_user_id == "555"
    assert cred.owner_name == "Bin Liang"


@pytest.mark.asyncio
async def test_bind_with_unresolvable_owner_username_still_succeeds(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """getChat failure must not block the bind — bot still works."""
    monkeypatch.setattr(
        cm_mod,
        "TelegramSDKClient",
        lambda token: _FakeTelegramClient(token, get_chat_lookup={}),
    )
    mgr = TelegramCredentialManager(db_client)

    res = await mgr.bind("agent_a", "123456789:AAH-tok-XXXXXXXXXXXXXXXXX", owner_username="@ghost")
    assert res["success"] is True
    assert res["data"]["owner_user_id"] == ""
    assert res["data"]["owner_name"] == ""

    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.owner_username == "ghost"
    assert cred.owner_user_id == ""


# ── get_public scrubs token ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_public_omits_token_fields(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)
    await mgr.bind("agent_a", "123456789:AAH-secret-XXXXXXXXXXXXXX")

    public = await mgr.get_public("agent_a")

    assert public is not None
    assert "bot_token" not in public
    assert "bot_token_encoded" not in public
    assert public["bot_user_id"] == "1001"
    assert public["bot_username"] == "acme_bot"


# ── unbind() ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unbind_removes_row(db_client, monkeypatch: pytest.MonkeyPatch):
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)
    await mgr.bind("agent_a", "123456789:AAH-tok-XXXXXXXXXXXXXXXXX")

    removed = await mgr.unbind("agent_a")

    assert removed is True
    assert (
        await db_client.get_one(
            "channel_telegram_credentials", {"agent_id": "agent_a"}
        )
        is None
    )


@pytest.mark.asyncio
async def test_unbind_returns_false_when_no_row(db_client):
    mgr = TelegramCredentialManager(db_client)
    assert await mgr.unbind("ghost") is False


# ── list_active filters enabled=1 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_list_active_filters_disabled_rows(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """Distinct bot identities — uniqueness on bot_user_id forbids sharing."""

    def factory(token: str) -> _FakeTelegramClient:
        bot_id = "100" if "on" in token else "200"
        return _FakeTelegramClient(
            token, bot_user_id=bot_id, bot_username=f"bot_{bot_id}"
        )

    monkeypatch.setattr(cm_mod, "TelegramSDKClient", factory)
    mgr = TelegramCredentialManager(db_client)

    await mgr.bind("agent_on", "123456789:AAH-tokon-XXXXXXXXXXXXXXX")
    await mgr.bind("agent_off", "123456790:AAH-tokoff-XXXXXXXXXXXXXX")
    await db_client.update(
        "channel_telegram_credentials", {"agent_id": "agent_off"}, {"enabled": 0}
    )

    active = await mgr.list_active()

    agent_ids = {c.agent_id for c in active}
    assert "agent_on" in agent_ids
    assert "agent_off" not in agent_ids


# ── update_owner — late owner resolution ───────────────────────────────


@pytest.mark.asyncio
async def test_update_owner_populates_fields(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """update_owner writes user_id + name and bumps updated_at.

    Called by TelegramTrigger when first DM arrives whose
    from.username matches the bind-time owner_username.
    """
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)
    await mgr.bind("agent_a", "123456789:AAH-tok-XXXXXXXXXXXXXXXXX", owner_username="ctong201")

    # Before: owner_user_id empty (Telegram getChat refused @username)
    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.owner_username == "ctong201"
    assert cred.owner_user_id == ""

    # Late resolution
    ok = await mgr.update_owner(
        "agent_a", owner_user_id="8612707834", owner_name="Chen Tong"
    )
    assert ok is True

    after = await mgr.get("agent_a")
    assert after is not None
    assert after.owner_user_id == "8612707834"
    assert after.owner_name == "Chen Tong"
    # owner_username stays — it's the lock
    assert after.owner_username == "ctong201"


@pytest.mark.asyncio
async def test_update_owner_returns_false_when_no_row(db_client):
    """No-op when the agent has no credential row."""
    mgr = TelegramCredentialManager(db_client)
    assert await mgr.update_owner("ghost", "x", "y") is False


@pytest.mark.asyncio
async def test_update_owner_is_cas_once_resolved(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """Once ``owner_user_id`` is set, ``update_owner`` MUST not silently
    overwrite it. Without the CAS, an attacker who briefly squatted the
    locked username could replace the legitimate owner's user_id after
    they had already DM'd the bot. The CAS makes the first writer win
    forever (until user re-binds)."""
    _patch_ok(monkeypatch)
    mgr = TelegramCredentialManager(db_client)
    await mgr.bind("agent_a", "123456789:AAH-tok-XXXXXXXXXXXXXXXXX", owner_username="ctong201")

    # Legitimate owner DMs first — wins the lock.
    first = await mgr.update_owner(
        "agent_a", owner_user_id="legit_user_id", owner_name="Real Owner"
    )
    assert first is True

    # Attacker (matching username) DMs second — must be rejected.
    second = await mgr.update_owner(
        "agent_a", owner_user_id="attacker_user_id", owner_name="Imposter"
    )
    assert second is False

    after = await mgr.get("agent_a")
    assert after is not None
    assert after.owner_user_id == "legit_user_id"
    assert after.owner_name == "Real Owner"
