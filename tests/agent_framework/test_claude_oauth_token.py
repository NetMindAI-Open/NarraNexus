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

from narranexus.platform.agent_framework.api_config import ClaudeConfig
from narranexus.platform.settings import settings

TOKEN = "sk-ant-oat01-test-token-value"


def _card(**kw):
    from narranexus.platform.agent_framework.providers.driver.base import ProviderCard

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
    from narranexus.platform.schema.provider_schema import AuthType

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
    from narranexus.platform.agent_framework.providers.model_catalog import (
        resolve_cli_alias,
    )

    assert resolve_cli_alias("opus", auth_type="oauth_token") == "opus"


# ---------------------------------------------------------------------------
# Billing: subscription token is Anthropic-billed, like oauth
# ---------------------------------------------------------------------------


def test_billing_policy_oauth_token_is_external_oauth():
    from narranexus.platform.agent_framework.providers.driver.derive import (
        derive_billing_policy,
    )

    assert derive_billing_policy("claude_oauth", "oauth_token") == "external_oauth"


# ---------------------------------------------------------------------------
# Driver: config builders pass the token through
# ---------------------------------------------------------------------------


def test_build_claude_config_passes_token_through():
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    cfg = ClaudeOAuthDriver(_card()).build_claude_config("opus")
    assert cfg.api_key == TOKEN
    assert cfg.auth_type == "oauth_token"
    assert cfg.supports_anthropic_server_tools is True


def test_build_cli_helper_config_passes_token_through():
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    cfg = ClaudeOAuthDriver(_card()).build_cli_helper_config("haiku")
    assert cfg.framework == "claude_code"
    assert cfg.auth_type == "oauth_token"
    assert cfg.api_key == TOKEN


def test_build_claude_config_oauth_unchanged():
    """The legacy host-CLI oauth path must keep its blank-key contract."""
    from narranexus_plugins.providers.claude_oauth import (
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
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    health = await ClaudeOAuthDriver(_card()).probe()
    assert health.ok is True
    # The stored secret must never surface in the probe detail.
    assert TOKEN not in health.detail


@pytest.mark.asyncio
async def test_probe_oauth_missing_auth_ref_detail_is_actionable():
    """A broken host-oauth row (no auth_ref sentinel) must tell the USER what
    to do — not leak the internal column name (ticket P1, 2026-08-27: the
    Test dialog showed "auth_ref is missing or not a claude-cli: reference"
    verbatim). With creation now writing the sentinel this only ever surfaces
    for genuinely corrupt rows, and the cure is recreating the provider."""
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    driver = ClaudeOAuthDriver(_card(auth_type="oauth", api_key="", auth_ref=""))
    health = await driver.probe()
    assert not health.ok
    assert "auth_ref" not in health.detail
    assert "re-add" in health.detail


@pytest.mark.asyncio
async def test_probe_misrouted_card_does_not_advise_removal():
    """test_provider's driver_type fallback routes any anthropic-protocol
    oauth row into ClaudeOAuthDriver — including a hand-crafted custom card
    (source='user') that was never a Claude Code (OAuth) card. Advising
    'remove and re-add Claude Code (OAuth)' there is destructive and wrong;
    the probe must say the card is misconfigured instead (review round 3,
    Minor 1)."""
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    driver = ClaudeOAuthDriver(
        _card(source="user", auth_type="oauth", api_key="", auth_ref="")
    )
    health = await driver.probe()
    assert not health.ok
    assert "remove" not in health.detail.lower()
    assert "auth_ref" not in health.detail


@pytest.mark.asyncio
async def test_probe_misrouted_token_card_gets_source_verdict_not_token_advice():
    """The source guard must fire BEFORE the token-mode branch: a
    hand-crafted custom card with auth_type='oauth_token' (route-layer
    free string) misrouted into this driver must get the 'not a Claude
    Code (OAuth) card' verdict, not 'run `claude setup-token`' advice for
    a card that setup-token cannot fix (review round 4, Minor 1)."""
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    driver = ClaudeOAuthDriver(
        _card(source="user", auth_type="oauth_token", api_key="", auth_ref="")
    )
    health = await driver.probe()
    assert not health.ok
    assert "setup-token" not in health.detail
    assert "not a Claude Code (OAuth) card" in health.detail


@pytest.mark.parametrize(
    ("source", "auth_type", "expected"),
    [
        # The only two cells that produce a sentinel:
        ("claude_oauth", "oauth", "claude-cli:~/.claude/.credentials.json"),
        ("codex_oauth", "oauth", "codex-cli:~/.codex/auth.json"),
        # Case-insensitive on both axes:
        ("Claude_OAuth", "OAuth", "claude-cli:~/.claude/.credentials.json"),
        # oauth_token: the token IS the credential — None, never "".
        ("claude_oauth", "oauth_token", None),
        # Non-CLI sources with a free-string oauth auth_type (the round-2
        # credential-confusion hole): must NOT inherit the host sentinel.
        ("user", "oauth", None),
        ("system", "oauth", None),
        (None, "oauth", None),
        ("", "oauth", None),
        # Non-oauth auth types never derive:
        ("claude_oauth", "api_key", None),
        ("codex_oauth", "", None),
        ("claude_oauth", None, None),
    ],
)
def test_derive_auth_ref_truth_table(source, auth_type, expected):
    """Direct table-driven pin on the single truth table every producer
    (insert, read-time, backfill, codex config builders) now consumes.
    Assertions use `is None`, not falsiness — three call sites depend on
    the None-vs-\"\" distinction (review round 5, Important 2)."""
    from narranexus.platform.agent_framework.providers.driver.derive import (
        derive_auth_ref,
    )

    result = derive_auth_ref(source, auth_type)
    if expected is None:
        assert result is None
    else:
        assert result == expected


def test_from_row_derives_missing_auth_ref_for_host_oauth_rows():
    """Rows inserted before 2026-08-27 carry auth_ref=NULL until a backend
    restart runs the startup backfill — ProviderCard.from_row derives the
    sentinel at read time so Test works on them immediately, the same
    read-time philosophy test_provider already applies to the sibling
    driver_type column. Persisting stays the backfill's job."""
    from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
    from narranexus.platform.agent_framework.providers.driver.derive import (
        CLAUDE_CLI_CREDENTIALS_REF,
        CODEX_CLI_CREDENTIALS_REF,
        resolve_claude_credentials_path,
    )

    claude = ProviderCard.from_row({
        "provider_id": "p1", "source": "claude_oauth",
        "protocol": "anthropic", "auth_type": "oauth", "auth_ref": None,
    })
    assert claude.auth_ref == CLAUDE_CLI_CREDENTIALS_REF
    assert resolve_claude_credentials_path(claude.auth_ref) is not None

    codex = ProviderCard.from_row({
        "provider_id": "p2", "source": "codex_oauth",
        "protocol": "openai", "auth_type": "oauth", "auth_ref": None,
    })
    assert codex.auth_ref == CODEX_CLI_CREDENTIALS_REF


def test_from_row_does_not_derive_auth_ref_for_non_cli_sources():
    """The derivation is scoped to the two CLI-subscription sources. A
    hand-crafted row (POST /api/providers with card_type=anthropic and a
    free-string auth_type="oauth") must NOT inherit the claude sentinel —
    that would let a card with no credential of its own verify green
    against the HOST's claude subscription (review round 2, Important 1)."""
    from narranexus.platform.agent_framework.providers.driver.base import ProviderCard

    card = ProviderCard.from_row({
        "provider_id": "p1", "source": "user",
        "protocol": "anthropic", "auth_type": "oauth", "auth_ref": None,
    })
    assert not card.auth_ref


@pytest.mark.asyncio
async def test_from_row_keeps_garbage_auth_ref_verbatim_so_probe_can_reject_it():
    """A non-empty but malformed auth_ref must survive from_row untouched and
    still reach the probe's actionable missing-reference message — proves
    that branch is not dead code after the read-time derivation landed
    (review round 2, Minor 4)."""
    from narranexus.platform.agent_framework.providers.driver.base import ProviderCard
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    card = ProviderCard.from_row({
        "provider_id": "p1", "source": "claude_oauth",
        "protocol": "anthropic", "auth_type": "oauth",
        "auth_ref": "claude-cli-legacy-typo",  # no "claude-cli:" prefix
    })
    assert card.auth_ref == "claude-cli-legacy-typo"

    health = await ClaudeOAuthDriver(card).probe()
    assert not health.ok
    assert "re-add" in health.detail


def test_from_row_does_not_derive_auth_ref_for_token_rows():
    """oauth_token rows carry no credential-file sentinel — the token IS the
    credential. Read-time derivation must not invent one, or token cards
    would walk into the file/Keychain probe branch."""
    from narranexus.platform.agent_framework.providers.driver.base import ProviderCard

    card = ProviderCard.from_row({
        "provider_id": "p1", "source": "claude_oauth",
        "protocol": "anthropic", "auth_type": "oauth_token",
        "api_key": TOKEN, "auth_ref": "",
    })
    assert not card.auth_ref


@pytest.mark.asyncio
async def test_probe_oauth_token_missing_is_not_ok():
    from narranexus_plugins.providers.claude_oauth import (
        ClaudeOAuthDriver,
    )

    health = await ClaudeOAuthDriver(_card(api_key="")).probe()
    assert health.ok is False


# ---------------------------------------------------------------------------
# Resolver: helper slot on an oauth_token card routes through the CLI helper
# ---------------------------------------------------------------------------


def test_resolver_routes_oauth_token_helper_to_cli():
    from narranexus.platform.agent_framework.providers.driver.resolver import (
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
    from narranexus.platform.agent_framework.providers.user_service import (
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
    assert row["driver_type"] == "claude_oauth"
    assert row["billing_policy"] == "external_oauth"
    assert not row.get("auth_ref")
    assert len(db.providers) == 1


@pytest.mark.asyncio
async def test_add_claude_oauth_without_token_still_rejects_duplicate():
    service, _db = _service()
    await service.add_provider("u1", card_type="claude_oauth")
    with pytest.raises(ValueError):
        await service.add_provider("u1", card_type="claude_oauth")


@pytest.mark.asyncio
async def test_add_claude_oauth_host_cli_row_is_complete_at_insert():
    """Host-CLI oauth rows must carry auth_ref / driver_type / billing_policy
    the moment they are inserted — NOT after the next backend restart.

    The startup backfill (backfill_provider_metadata, backend/main.py) exists
    to normalize rows left by pre-driver builds; it must never be the writer
    of record for new rows. Leaning on it left a freshly created card with
    auth_ref=NULL until restart, so Test right after creation always failed
    with "auth_ref is missing or not a claude-cli: reference" (P1,
    2026-08-27) — while the codex_oauth and setup-token branches already
    wrote all three fields at insert time.
    """
    from narranexus.platform.agent_framework.providers.driver.derive import (
        CLAUDE_CLI_CREDENTIALS_REF,
    )

    service, db = _service()
    _, new_ids = await service.add_provider("u1", card_type="claude_oauth")
    row = db.providers[new_ids[0]]
    assert row["auth_type"] == "oauth"
    assert row["auth_ref"] == CLAUDE_CLI_CREDENTIALS_REF
    assert row["driver_type"] == "claude_oauth"
    assert row["billing_policy"] == "external_oauth"


def test_cli_subscription_row_fields_raises_on_out_of_scope_source():
    """The guard enforces the documented SCOPE, not mere classifiability:
    derive_driver_type happily classifies source='user' as custom_anthropic,
    so a None check alone would let the helper stamp external_oauth billing
    onto a card it must not serve."""
    from narranexus.platform.agent_framework.providers.user_service import (
        _cli_subscription_row_fields,
    )

    with pytest.raises(ValueError):
        _cli_subscription_row_fields("user", "oauth", "anthropic")


def test_cli_subscription_row_fields_raises_on_half_wired_source(monkeypatch):
    """The gemini_cli drift scenario: a source added to
    CLI_SUBSCRIPTION_SOURCES before derive_driver_type grew its branch must
    raise loudly — otherwise _insert_provider would write driver_type=NULL,
    the exact state this helper exists to eliminate."""
    from narranexus.platform.agent_framework.providers.driver import derive
    from narranexus.platform.agent_framework.providers.user_service import (
        _cli_subscription_row_fields,
    )

    monkeypatch.setattr(
        derive,
        "CLI_SUBSCRIPTION_SOURCES",
        frozenset({"claude_oauth", "codex_oauth", "gemini_cli"}),
    )
    with pytest.raises(ValueError, match="derive_driver_type"):
        _cli_subscription_row_fields("gemini_cli", "oauth", "openai")


# ---------------------------------------------------------------------------
# test_provider: oauth_token rows get a LIVE verification, not a static ✓
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_provider_routes_oauth_token_to_live_verify(monkeypatch):
    """The 2026-07-23 incident's probe lied ("logged in ✓") because it only
    checked credential existence. For token rows the credential is in OUR
    hands, so the explicit test button must make a real end-to-end CLI call."""
    from narranexus_plugins.providers import (
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
    from narranexus_plugins.providers import (
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
