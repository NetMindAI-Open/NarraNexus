"""
@file_name: test_slack_credential_manager.py
@date: 2026-05-08
@description: Unit tests for SlackCredentialManager — the per-agent
binding store backing channel_slack_credentials.

Why this file exists:
    Slack credentials carry both bot_token (xoxb-...) and app-level
    token (xapp-...). The manager must validate prefix + auth.test
    BEFORE persisting, must scrub tokens out of public views, and
    must round-trip base64 encoding without corrupting payloads.
    These tests cover those invariants.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.slack_module import _slack_credential_manager as cm_mod
from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredentialManager,
    _decode_token,
    _encode_token,
)
from xyz_agent_context.module.slack_module.slack_sdk_client import SlackSDKError


class _FakeSlackClient:
    """Stand-in for SlackSDKClient that returns a canned auth.test."""

    def __init__(
        self,
        bot_token: str,
        *,
        raise_code: str | None = None,
        team_id: str = "T0001",
        bot_user_id: str = "U0BOT",
        owner_lookup: dict | None = None,
    ):
        self._bot_token = bot_token
        self._raise_code = raise_code
        self._team_id = team_id
        self._bot_user_id = bot_user_id
        self._owner_lookup = owner_lookup or {}

    async def auth_test(self) -> dict:
        if self._raise_code:
            raise SlackSDKError(self._raise_code, f"auth.test failed: {self._raise_code}")
        return {
            "team_id": self._team_id,
            "team": "Acme Workspace",
            "user_id": self._bot_user_id,
            "user": "acmebot",
        }

    async def lookup_user_by_email(self, email: str) -> dict:
        return self._owner_lookup.get(email, {})


def _patch_auth_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default fake — every bind resolves to the SAME bot identity
    (T0001 / U0BOT). Tests that bind multiple agents must override
    explicitly via ``monkeypatch.setattr(cm_mod, "SlackSDKClient", ...)``
    to avoid tripping the (team_id, bot_user_id) uniqueness constraint."""
    monkeypatch.setattr(
        cm_mod, "SlackSDKClient", lambda token: _FakeSlackClient(token)
    )


def _patch_auth_fail(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    monkeypatch.setattr(
        cm_mod,
        "SlackSDKClient",
        lambda token: _FakeSlackClient(token, raise_code=code),
    )


# ── Encoding round-trip ────────────────────────────────────────────────


def test_encode_decode_round_trip():
    raw = "xoxb-12345-secretpayload"
    encoded = _encode_token(raw)
    assert encoded != raw
    assert _decode_token(encoded) == raw


def test_encode_empty_returns_empty():
    assert _encode_token("") == ""
    assert _decode_token("") == ""


# ── bind() ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_valid_inserts_row_and_returns_team_metadata(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)

    result = await mgr.bind("agent_a", "xoxb-real-token", "xapp-real-token")

    assert result["success"] is True
    assert result["data"]["team_id"] == "T0001"
    assert result["data"]["team_name"] == "Acme Workspace"
    assert result["data"]["bot_user_id"] == "U0BOT"

    # Row landed in DB and tokens are NOT plaintext at rest.
    row = await db_client.get_one("channel_slack_credentials", {"agent_id": "agent_a"})
    assert row is not None
    assert row["bot_token_encoded"] != "xoxb-real-token"
    assert row["app_token_encoded"] != "xapp-real-token"
    assert _decode_token(row["bot_token_encoded"]) == "xoxb-real-token"


@pytest.mark.asyncio
async def test_bind_rejects_invalid_bot_token_prefix(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)

    result = await mgr.bind("agent_a", "wrong-prefix", "xapp-token")

    assert result["success"] is False
    assert "xoxb-" in result["error"]
    # Nothing persisted
    row = await db_client.get_one("channel_slack_credentials", {"agent_id": "agent_a"})
    assert row is None


@pytest.mark.asyncio
async def test_bind_rejects_missing_app_token(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)

    # bot_token only — no app_token, must fail prefix check
    result = await mgr.bind("agent_a", "xoxb-only", "")

    assert result["success"] is False
    assert "xapp-" in result["error"]


@pytest.mark.asyncio
async def test_bind_propagates_auth_test_failure(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_fail(monkeypatch, "invalid_auth")
    mgr = SlackCredentialManager(db_client)

    result = await mgr.bind("agent_a", "xoxb-bad", "xapp-bad")

    assert result["success"] is False
    # Friendly mapping (added 2026-05-12) — surface user-actionable text,
    # not the raw Slack code. Old assertion was ``"invalid_auth" in error``.
    assert "Bot Token" in result["error"] and (
        "invalid" in result["error"].lower() or "revoked" in result["error"].lower()
    )
    row = await db_client.get_one("channel_slack_credentials", {"agent_id": "agent_a"})
    assert row is None


@pytest.mark.asyncio
async def test_bind_rebind_updates_existing_row(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)

    await mgr.bind("agent_a", "xoxb-first", "xapp-first")
    await mgr.bind("agent_a", "xoxb-second", "xapp-second")

    rows = await db_client.get("channel_slack_credentials", {"agent_id": "agent_a"})
    assert len(rows) == 1
    assert _decode_token(rows[0]["bot_token_encoded"]) == "xoxb-second"


# ── get_public never returns tokens ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_public_omits_token_fields(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)
    await mgr.bind("agent_a", "xoxb-secret", "xapp-secret")

    public = await mgr.get_public("agent_a")

    assert public is not None
    assert "bot_token" not in public
    assert "app_token" not in public
    assert "bot_token_encoded" not in public
    assert "app_token_encoded" not in public
    assert public["team_id"] == "T0001"
    assert public["bot_user_id"] == "U0BOT"


@pytest.mark.asyncio
async def test_get_public_returns_none_when_unbound(db_client):
    mgr = SlackCredentialManager(db_client)
    assert await mgr.get_public("ghost") is None


# ── unbind() ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unbind_removes_row(db_client, monkeypatch: pytest.MonkeyPatch):
    _patch_auth_ok(monkeypatch)
    mgr = SlackCredentialManager(db_client)
    await mgr.bind("agent_a", "xoxb-x", "xapp-x")

    removed = await mgr.unbind("agent_a")

    assert removed is True
    assert (
        await db_client.get_one("channel_slack_credentials", {"agent_id": "agent_a"})
        is None
    )


@pytest.mark.asyncio
async def test_unbind_returns_false_when_no_row(db_client):
    mgr = SlackCredentialManager(db_client)
    assert await mgr.unbind("ghost") is False


# ── list_active filters enabled=1 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_list_active_filters_disabled_rows(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    # Distinct bot identities per agent — required because the uniqueness
    # constraint forbids two agents sharing one (team_id, bot_user_id).
    def factory(token: str) -> _FakeSlackClient:
        bot = "U_ON" if "on" in token else "U_OFF"
        return _FakeSlackClient(token, bot_user_id=bot)

    monkeypatch.setattr(cm_mod, "SlackSDKClient", factory)
    mgr = SlackCredentialManager(db_client)
    await mgr.bind("agent_on", "xoxb-on", "xapp-on")
    await mgr.bind("agent_off", "xoxb-off", "xapp-off")
    # Manually disable the second one
    await db_client.update(
        "channel_slack_credentials", {"agent_id": "agent_off"}, {"enabled": 0}
    )

    active = await mgr.list_active()

    agent_ids = {c.agent_id for c in active}
    assert "agent_on" in agent_ids
    assert "agent_off" not in agent_ids


# ── Bot uniqueness across agents ───────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_rejects_same_bot_for_different_agent(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """Same (team_id, bot_user_id) cannot be bound to two agents."""
    # Force both binds to resolve to the SAME bot identity
    monkeypatch.setattr(
        cm_mod,
        "SlackSDKClient",
        lambda token: _FakeSlackClient(
            token, team_id="T_SHARED", bot_user_id="U_SHARED"
        ),
    )
    mgr = SlackCredentialManager(db_client)

    first = await mgr.bind("agent_a", "xoxb-aa", "xapp-aa")
    assert first["success"] is True

    second = await mgr.bind("agent_b", "xoxb-bb", "xapp-bb")
    assert second["success"] is False
    assert "already bound to another agent" in second["error"]
    # Should mention agent_a so the user knows where to unbind
    assert "agent_a" in second["error"]

    # And no row was inserted for agent_b
    cred_b = await mgr.get("agent_b")
    assert cred_b is None


@pytest.mark.asyncio
async def test_bind_same_bot_same_agent_is_rebind_not_conflict(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """Re-binding the same bot to the SAME agent must succeed (upsert path)."""
    monkeypatch.setattr(
        cm_mod,
        "SlackSDKClient",
        lambda token: _FakeSlackClient(
            token, team_id="T_REBIND", bot_user_id="U_REBIND"
        ),
    )
    mgr = SlackCredentialManager(db_client)

    first = await mgr.bind("agent_a", "xoxb-1", "xapp-1")
    assert first["success"] is True

    # Same agent, same bot — upsert with refreshed tokens
    second = await mgr.bind("agent_a", "xoxb-2", "xapp-2")
    assert second["success"] is True


# ── Owner resolution via email ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_with_owner_email_resolves_user_id(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """When owner_email is supplied and the lookup succeeds, owner fields
    are populated on the credential row."""
    def factory(token: str) -> _FakeSlackClient:
        return _FakeSlackClient(
            token,
            owner_lookup={
                "owner@example.com": {
                    "id": "U_OWNER",
                    "real_name": "Bin Liang",
                },
            },
        )

    monkeypatch.setattr(cm_mod, "SlackSDKClient", factory)
    mgr = SlackCredentialManager(db_client)

    res = await mgr.bind(
        "agent_a", "xoxb-x", "xapp-x", owner_email="owner@example.com"
    )
    assert res["success"] is True
    assert res["data"]["owner_user_id"] == "U_OWNER"
    assert res["data"]["owner_name"] == "Bin Liang"

    # And it round-trips through .get() too
    cred = await mgr.get("agent_a")
    assert cred is not None
    assert cred.owner_email == "owner@example.com"
    assert cred.owner_user_id == "U_OWNER"
    assert cred.owner_name == "Bin Liang"


@pytest.mark.asyncio
async def test_bind_with_unresolvable_owner_email_still_succeeds(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """A lookup miss is logged but does NOT fail the bind — bot still works,
    just without the trust signal."""
    def factory(token: str) -> _FakeSlackClient:
        # owner_lookup empty → every email returns {}
        return _FakeSlackClient(token, owner_lookup={})

    monkeypatch.setattr(cm_mod, "SlackSDKClient", factory)
    mgr = SlackCredentialManager(db_client)

    res = await mgr.bind(
        "agent_a", "xoxb-x", "xapp-x", owner_email="ghost@example.com"
    )
    assert res["success"] is True
    assert res["data"]["owner_user_id"] == ""
    assert res["data"]["owner_name"] == ""

    cred = await mgr.get("agent_a")
    assert cred is not None
    # Email is still recorded so the user can see what they tried.
    assert cred.owner_email == "ghost@example.com"
    assert cred.owner_user_id == ""
