"""
@file_name: test_claude_oauth_token.py
@author: Bin Liang
@date: 2026-07-26
@description: Claude subscription auth via a long-lived setup-token
(``claude setup-token`` → ``CLAUDE_CODE_OAUTH_TOKEN``), auth_type
``oauth_token``.

Why this path exists (2026-07-23 incident): on macOS the Claude Code CLI
imports the staged ``.credentials.json`` into a config-dir-namespaced
Keychain entry ONCE and then only ever reads the Keychain — the platform's
newest-wins file staging never reaches the CLI again, and the frozen token
copy dies as the host's OAuth family rotates. Every symptom-level fix
(re-stage, re-login, delete file) writes to a store the CLI does not read.
The cure is the officially documented headless channel: a one-year token
minted by ``claude setup-token``, injected as the ``CLAUDE_CODE_OAUTH_TOKEN``
env var (auth precedence #5 — beats any Keychain state), no staging, no
Keychain, no rotation.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.settings import settings

TOKEN = "sk-ant-oat01-test-token-value"


def _card(**kw):
    from xyz_agent_context.agent_framework.providers.driver.base import ProviderCard

    base = dict(
        provider_id="p1", user_id="u1", name="Claude Code (OAuth)",
        source="claude_oauth", protocol="anthropic", auth_type="oauth_token",
        api_key=TOKEN, base_url="", models=["opus", "sonnet", "haiku"],
        driver_type="claude_oauth", auth_ref="",
    )
    base.update(kw)
    return ProviderCard(**base)


# ---------------------------------------------------------------------------
# Schema: the new auth_type enum value
# ---------------------------------------------------------------------------


def test_auth_type_enum_has_oauth_token():
    from xyz_agent_context.schema.provider_schema import AuthType

    assert AuthType.OAUTH_TOKEN.value == "oauth_token"


# ---------------------------------------------------------------------------
# ClaudeConfig.to_cli_env: env injection + inheritance blocking
# ---------------------------------------------------------------------------


def test_oauth_token_env_injects_token_and_blanks_anthropic_vars():
    """The token rides CLAUDE_CODE_OAUTH_TOKEN; ANTHROPIC_* must stay blank
    (they sit ABOVE the oauth token in the CLI's auth precedence and would
    hijack the run if a stray value leaked through)."""
    env = ClaudeConfig(api_key=TOKEN, auth_type="oauth_token").to_cli_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""


def test_oauth_token_uses_keyed_config_dir_not_staging_dir():
    """oauth_token is env-injected like keyed auth — it must NOT run against
    the oauth staging dir (whose Keychain-namespaced entries are the exact
    failure the token path exists to escape)."""
    env = ClaudeConfig(api_key=TOKEN, auth_type="oauth_token").to_cli_env()
    assert env["CLAUDE_CONFIG_DIR"] == settings.claude_cli_config_path
    assert env["CLAUDE_CONFIG_DIR"] != settings.claude_oauth_config_path


def test_other_auth_types_blank_oauth_token_env():
    """Complete-dict contract: every auth type must carry the key with an
    explicit blank so a stray parent-process CLAUDE_CODE_OAUTH_TOKEN can't
    leak into the subprocess via the SDK's {**os.environ, **env} merge."""
    for auth_type, key in (("api_key", "k"), ("bearer_token", "k"), ("oauth", "")):
        env = ClaudeConfig(api_key=key, auth_type=auth_type).to_cli_env()
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


# ---------------------------------------------------------------------------
# Model alias handling: token path is a CLI transport, aliases stay verbatim
# ---------------------------------------------------------------------------


def test_resolve_cli_alias_keeps_alias_for_oauth_token():
    from xyz_agent_context.agent_framework.providers.model_catalog import (
        resolve_cli_alias,
    )

    assert resolve_cli_alias("opus", auth_type="oauth_token") == "opus"


# ---------------------------------------------------------------------------
# Billing: subscription token is Anthropic-billed, like oauth
# ---------------------------------------------------------------------------


def test_billing_policy_oauth_token_is_external_oauth():
    from xyz_agent_context.agent_framework.providers.driver.derive import (
        derive_billing_policy,
    )

    assert derive_billing_policy("claude_oauth", "oauth_token") == "external_oauth"


# ---------------------------------------------------------------------------
# Driver: config builders pass the token through
# ---------------------------------------------------------------------------


def test_build_claude_config_passes_token_through():
    from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    cfg = ClaudeOAuthDriver(_card()).build_claude_config("opus")
    assert cfg.api_key == TOKEN
    assert cfg.auth_type == "oauth_token"
    assert cfg.supports_anthropic_server_tools is True


def test_build_cli_helper_config_passes_token_through():
    from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    cfg = ClaudeOAuthDriver(_card()).build_cli_helper_config("haiku")
    assert cfg.framework == "claude_code"
    assert cfg.auth_type == "oauth_token"
    assert cfg.api_key == TOKEN


def test_build_claude_config_oauth_unchanged():
    """The legacy host-CLI oauth path must keep its blank-key contract."""
    from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    card = _card(auth_type="oauth", api_key="",
                 auth_ref="claude-cli:~/.claude/.credentials.json")
    cfg = ClaudeOAuthDriver(card).build_claude_config("opus")
    assert cfg.api_key == ""
    assert cfg.auth_type == "oauth"


# ---------------------------------------------------------------------------
# Driver: probe for oauth_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_oauth_token_present_is_ok():
    from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    health = await ClaudeOAuthDriver(_card()).probe()
    assert health.ok is True
    # The stored secret must never surface in the probe detail.
    assert TOKEN not in health.detail


@pytest.mark.asyncio
async def test_probe_oauth_token_missing_is_not_ok():
    from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    health = await ClaudeOAuthDriver(_card(api_key="")).probe()
    assert health.ok is False


# ---------------------------------------------------------------------------
# Resolver: helper slot on an oauth_token card routes through the CLI helper
# ---------------------------------------------------------------------------


def test_resolver_routes_oauth_token_helper_to_cli():
    from xyz_agent_context.agent_framework.providers.driver.resolver import (
        _resolve_slot_target,
    )

    method, key = _resolve_slot_target("helper_llm", "claude_code", _card())
    assert method == "build_cli_helper_config"
    assert key == "cli_helper"


# ---------------------------------------------------------------------------
# UserProviderService: save + reconnect flows
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self):
        self.providers: dict[str, dict] = {}
        self.slots: dict[tuple, dict] = {}

    async def get(self, table, filters=None):
        filters = filters or {}
        rows = (
            list(self.providers.values()) if table == "user_providers"
            else list(self.slots.values()) if table == "user_slots"
            else []
        )
        return [r for r in rows if all(r.get(k) == v for k, v in filters.items())]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        if table == "user_providers":
            self.providers[data["provider_id"]] = dict(data)
        elif table == "user_slots":
            self.slots[(data["user_id"], data["slot_name"])] = dict(data)

    async def update(self, table, filters, data):
        for r in await self.get(table, filters):
            r.update(data)
        return 1

    async def delete(self, table, filters):
        return 0


def _service():
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    db = _FakeDB()
    return UserProviderService(db), db


@pytest.mark.asyncio
async def test_add_claude_oauth_with_token_inserts_token_row():
    service, db = _service()
    _, new_ids = await service.add_provider(
        "u1", card_type="claude_oauth", api_key=TOKEN
    )
    row = db.providers[new_ids[0]]
    assert row["auth_type"] == "oauth_token"
    assert row["api_key"] == TOKEN
    assert row["driver_type"] == "claude_oauth"
    assert row["billing_policy"] == "external_oauth"
    # Token rows carry no credential-file sentinel — the token IS the credential.
    assert not row.get("auth_ref")
    assert json.loads(row["models"]) == ["opus", "sonnet", "haiku"]


@pytest.mark.asyncio
async def test_add_claude_oauth_with_token_binds_both_slots():
    service, db = _service()
    _, new_ids = await service.add_provider(
        "u1", card_type="claude_oauth", api_key=TOKEN
    )
    assert db.slots[("u1", "agent")]["provider_id"] == new_ids[0]
    assert db.slots[("u1", "helper_llm")]["provider_id"] == new_ids[0]


@pytest.mark.asyncio
async def test_add_claude_oauth_token_reconnects_existing_row_in_place():
    """Pasting a token while a claude_oauth card already exists must UPGRADE
    that card (the 2026-07-23 macOS Keychain recovery path), not raise the
    duplicate error — and must keep the provider_id so slot bindings survive."""
    service, db = _service()
    _, first_ids = await service.add_provider("u1", card_type="claude_oauth")
    pid = first_ids[0]
    assert db.providers[pid]["auth_type"] == "oauth"

    _, second_ids = await service.add_provider(
        "u1", card_type="claude_oauth", api_key=TOKEN
    )
    assert second_ids == [pid]
    row = db.providers[pid]
    assert row["auth_type"] == "oauth_token"
    assert row["api_key"] == TOKEN
    assert row["billing_policy"] == "external_oauth"
    assert not row.get("auth_ref")
    assert len(db.providers) == 1


@pytest.mark.asyncio
async def test_add_claude_oauth_without_token_still_rejects_duplicate():
    service, _db = _service()
    await service.add_provider("u1", card_type="claude_oauth")
    with pytest.raises(ValueError):
        await service.add_provider("u1", card_type="claude_oauth")


# ---------------------------------------------------------------------------
# test_provider: oauth_token rows get a LIVE verification, not a static ✓
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_provider_routes_oauth_token_to_live_verify(monkeypatch):
    """The 2026-07-23 incident's probe lied ("logged in ✓") because it only
    checked credential existence. For token rows the credential is in OUR
    hands, so the explicit test button must make a real end-to-end CLI call."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as claude_oauth_mod,
    )

    called = {}

    async def _fake_verify(self):
        called["hit"] = True
        return "ok", "live CLI call succeeded"

    monkeypatch.setattr(
        claude_oauth_mod.ClaudeOAuthDriver, "verify_live", _fake_verify
    )

    service, db = _service()
    _, new_ids = await service.add_provider(
        "u1", card_type="claude_oauth", api_key=TOKEN
    )
    ok, detail = await service.test_provider("u1", new_ids[0])
    assert called.get("hit") is True
    assert ok is True


@pytest.mark.asyncio
async def test_test_provider_oauth_also_runs_live_verify(monkeypatch):
    """REVERSED contract (2026-07-31 codex P0): host-CLI oauth rows used to
    keep a static unconditional pass — which is exactly how expired CLI
    credentials tested green and ProviderReadiness re-armed jobs onto them.
    Host oauth now runs the same live verification as oauth_token."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as claude_oauth_mod,
    )

    called = {}

    async def _fake_verify(self):
        called["hit"] = True
        return "dead", "host CLI credentials expired — run `claude login`"

    monkeypatch.setattr(
        claude_oauth_mod.ClaudeOAuthDriver, "verify_live", _fake_verify
    )

    service, _db = _service()
    _, new_ids = await service.add_provider("u1", card_type="claude_oauth")
    ok, detail = await service.test_provider("u1", new_ids[0])
    assert called.get("hit") is True
    assert ok is False
    assert "expired" in detail
