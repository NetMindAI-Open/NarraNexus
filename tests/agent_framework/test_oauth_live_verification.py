"""
@file_name: test_oauth_live_verification.py
@author: NarraNexus
@date: 2026-07-31
@description: The Test button must not lie about host-CLI OAuth credentials.

P0 bug: ``user_service.test_provider`` answered ``auth_type == "oauth"``
with an unconditional ``True`` — expired codex (or claude) CLI
credentials still showed "usable", and ``ProviderReadiness`` (which
delegates here) re-armed paused jobs onto dead credentials.

Contract pinned here (post PR #224 review):
- ``verify_live`` is TRI-STATE: "ok" / "dead" / "unknown". Only a
  verified-dead credential may block; environment gaps (control-plane
  node, timeout, missing model list) are "unknown" and map to
  True-with-caveat in ``test_provider`` so readiness recovery is never
  blocked over a situation nobody verified.
- oauth rows delegate to the driver's ``verify_live`` — a real one-shot
  through the same CLI transport the agent uses. No unconditional pass.
- codex rows resolve the codex driver even when driver_type is missing
  (legacy rows): protocol openai → codex_oauth, else claude_oauth.
- claude host-oauth STAGES the host credential into the isolated
  CLAUDE_CONFIG_DIR before spawning (review Critical: without staging a
  freshly-logged-in healthy credential verifies dead).
- ``registry.test_provider`` (transient-config path, api-key onboarding
  only) fails CLOSED for oauth configs instead of failing open.
"""
from __future__ import annotations

import sys
import types

import pytest
import pytest_asyncio

from xyz_agent_context.agent_framework.providers.driver.base import (
    VERIFY_DEAD,
    VERIFY_OK,
    VERIFY_UNKNOWN,
    ProviderCard,
)
from xyz_agent_context.agent_framework.providers.driver.drivers.codex_oauth import (
    CodexOAuthDriver,
)
from xyz_agent_context.agent_framework.providers.driver.drivers.claude_oauth import (
    ClaudeOAuthDriver,
)


@pytest.fixture(autouse=True)
def _local_node(monkeypatch):
    """Tests model the LOCAL install (the P0's environment) by default —
    the executor seam (both spellings) must read as absent."""
    monkeypatch.delenv("AGENT_EXECUTOR_URL", raising=False)
    monkeypatch.delenv("BROKER_URL", raising=False)


def _codex_card(**overrides) -> ProviderCard:
    base = dict(
        provider_id="prov_codex",
        user_id="user_x",
        name="Codex CLI",
        source="user",
        protocol="openai",
        auth_type="oauth",
        api_key="",
        base_url="",
        models=["gpt-5.1-codex"],
        driver_type="codex_oauth",
        auth_ref="codex-cli:~/.codex/auth.json",
    )
    base.update(overrides)
    return ProviderCard(**base)


def _claude_card(**overrides) -> ProviderCard:
    base = dict(
        provider_id="prov_claude",
        user_id="user_x",
        name="Claude Code Login",
        source="user",
        protocol="anthropic",
        auth_type="oauth",
        api_key="",
        base_url="",
        models=["opus"],
        driver_type="claude_oauth",
        auth_ref="claude-cli:~/.claude/.credentials.json",
    )
    base.update(overrides)
    return ProviderCard(**base)


async def _no_keychain() -> bool:
    return False


# ---------------------------------------------------------------------------
# CodexOAuthDriver.verify_live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_codex_verify_live_dead_without_credentials(monkeypatch, tmp_path):
    """Missing auth.json → verified-dead, no CLI spawn."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(
        mod, "resolve_codex_credentials_path", lambda ref: tmp_path / "absent.json"
    )
    spawned = []
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.get_agent_loop_driver",
        lambda **kw: spawned.append(kw),
    )

    verdict, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_DEAD
    assert "not found" in msg
    assert spawned == []


@pytest.mark.asyncio
async def test_codex_verify_live_dead_without_cli(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(mod, "resolve_codex_credentials_path", lambda ref: auth)
    monkeypatch.setattr("shutil.which", lambda name: None)

    verdict, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_DEAD
    assert "codex CLI not found" in msg


@pytest.mark.asyncio
async def test_codex_verify_live_unknown_on_control_plane(monkeypatch):
    """BROKER_URL set = this container never runs the CLI (that is the env
    dev/prod compose actually sets — the first guard keyed on
    AGENT_EXECUTOR_URL alone and was dead on cloud, review round 3). Local
    state must not even be inspected — the verdict is undecidable here."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    inspected = []
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(
        mod, "resolve_codex_credentials_path", lambda ref: inspected.append(ref)
    )

    verdict, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_UNKNOWN
    assert "control plane" in msg
    assert inspected == []


class _FakeDriver:
    """Stands in for the codex agent-loop driver: replays canned events."""

    def __init__(self, events):
        self._events = events

    def agent_loop(self, **kwargs):
        async def _gen():
            for ev in self._events:
                yield ev

        return _gen()


def _wire_codex_oneshot(monkeypatch, tmp_path, events):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    monkeypatch.setattr(mod, "resolve_codex_credentials_path", lambda ref: auth)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")

    import xyz_agent_context.agent_framework as fw

    monkeypatch.setattr(
        fw, "get_agent_loop_driver", lambda **kw: _FakeDriver(events)
    )


@pytest.mark.asyncio
async def test_codex_verify_live_reports_unauthorized_as_dead(monkeypatch, tmp_path):
    """The codex CLI surfaces dead credentials as a terminal error EVENT —
    exactly the case the old unconditional True papered over."""
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [
            {
                "type": "raw_response_event",
                "data": {
                    "type": "response.error",
                    "error_type": "unauthorized",
                    "error_message": "access token could not be refreshed",
                },
            }
        ],
    )

    verdict, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_DEAD
    assert "unauthorized" in msg


@pytest.mark.asyncio
async def test_codex_verify_live_ok_on_real_reply(monkeypatch, tmp_path):
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [
            {
                "type": "raw_response_event",
                "data": {"type": "response.text.delta", "delta": "OK"},
            },
            {"type": "raw_response_event", "data": {"type": "response.done"}},
        ],
    )

    verdict, msg = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_OK
    assert "live" in msg.lower()


@pytest.mark.asyncio
async def test_codex_verify_live_silence_is_dead(monkeypatch, tmp_path):
    """A run that produces neither text nor an error event must not pass."""
    _wire_codex_oneshot(
        monkeypatch,
        tmp_path,
        [{"type": "raw_response_event", "data": {"type": "response.done"}}],
    )

    verdict, _ = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_DEAD


@pytest.mark.asyncio
async def test_codex_verify_live_uses_curated_default_model(monkeypatch, tmp_path):
    """The verification model must come from the catalog's curated list,
    not the stored models column (dead pinned ids must not read as dead
    credentials). Guards the review finding that the curated lookup was
    dead code returning []."""
    captured = {}

    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        codex_oauth as mod,
    )

    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    monkeypatch.setattr(mod, "resolve_codex_credentials_path", lambda ref: auth)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/codex")

    import xyz_agent_context.agent_framework as fw

    monkeypatch.setattr(
        fw,
        "get_agent_loop_driver",
        lambda **kw: _FakeDriver(
            [{"type": "raw_response_event", "data": {"type": "response.text.delta", "delta": "OK"}}]
        ),
    )

    driver = CodexOAuthDriver(_codex_card(models=["gpt-ancient-retired"]))
    orig_build = driver.build_codex_config

    def spy_build(model, **kw):
        captured["model"] = model
        return orig_build(model, **kw)

    monkeypatch.setattr(driver, "build_codex_config", spy_build)

    verdict, _ = await driver.verify_live()

    assert verdict == VERIFY_OK
    from xyz_agent_context.agent_framework.providers.model_catalog import (
        get_default_models,
    )

    curated = get_default_models("codex_oauth", "openai")
    assert curated, "curated codex defaults must exist in the catalog"
    assert captured["model"] == curated[0]
    assert captured["model"] != "gpt-ancient-retired"


# ---------------------------------------------------------------------------
# ClaudeOAuthDriver.verify_live — host-oauth mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_host_oauth_verify_live_dead_without_credentials(
    monkeypatch, tmp_path
):
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as mod,
    )

    monkeypatch.setattr(
        mod, "resolve_claude_credentials_path", lambda ref: tmp_path / "absent.json"
    )
    monkeypatch.setattr(
        ClaudeOAuthDriver, "_keychain_has_credentials", staticmethod(_no_keychain)
    )

    verdict, msg = await ClaudeOAuthDriver(_claude_card()).verify_live()

    assert verdict == VERIFY_DEAD
    assert "not found" in msg


@pytest.mark.asyncio
async def test_claude_host_oauth_verify_live_stages_then_succeeds(
    monkeypatch, tmp_path
):
    """Review Critical: host-oauth points CLAUDE_CONFIG_DIR at the ISOLATED
    dir, whose credentials only exist after staging. A healthy host login
    must verify OK — which requires the staging call the agent adapter
    makes before every spawn."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as mod,
    )
    from xyz_agent_context.agent_framework.adapters.claude import sdk as claude_sdk

    creds = tmp_path / ".credentials.json"
    creds.write_text("{}")
    monkeypatch.setattr(mod, "resolve_claude_credentials_path", lambda ref: creds)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")

    staged = []
    monkeypatch.setattr(
        claude_sdk,
        "_stage_claude_oauth_credentials",
        lambda config_dir: staged.append(str(config_dir)),
    )

    # Stub the claude SDK so the one-shot "replies" without spawning.
    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _AssistantMessage:
        def __init__(self):
            self.content = [_TextBlock("OK")]

    async def _fake_query(*, prompt, options):
        yield _AssistantMessage()

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=_AssistantMessage,
        TextBlock=_TextBlock,
        ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
        query=_fake_query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    verdict, msg = await ClaudeOAuthDriver(_claude_card()).verify_live()

    assert verdict == VERIFY_OK
    assert staged, "host-oauth verification must stage credentials before spawning"


@pytest.mark.asyncio
async def test_claude_verify_live_unknown_on_control_plane(monkeypatch):
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    verdict, msg = await ClaudeOAuthDriver(_claude_card()).verify_live()

    assert verdict == VERIFY_UNKNOWN
    assert "control plane" in msg


@pytest.mark.asyncio
async def test_codex_verify_live_unknown_with_static_executor_env(monkeypatch):
    """The static AGENT_EXECUTOR_URL spelling still counts as the seam."""
    monkeypatch.setenv("AGENT_EXECUTOR_URL", "http://executor:8020")

    verdict, _ = await CodexOAuthDriver(_codex_card()).verify_live()

    assert verdict == VERIFY_UNKNOWN


@pytest.mark.asyncio
async def test_claude_token_mode_verifies_on_control_plane(monkeypatch):
    """Review round 3: token mode is node-independent — the credential rides
    the env and the backend image ships the claude CLI. The seam guard must
    NOT demote a working control-plane verification to unknown (it has been
    verifying there since 2026-07-23)."""
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")

    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _AssistantMessage:
        def __init__(self):
            self.content = [_TextBlock("OK")]

    async def _fake_query(*, prompt, options):
        yield _AssistantMessage()

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=_AssistantMessage,
        TextBlock=_TextBlock,
        ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
        query=_fake_query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    card = _claude_card(auth_type="oauth_token", api_key="sk-ant-oat01-test")
    verdict, _ = await ClaudeOAuthDriver(card).verify_live()

    assert verdict == VERIFY_OK


@pytest.mark.asyncio
async def test_claude_cli_not_found_is_unknown(monkeypatch, tmp_path):
    """A missing/broken CLI install (SDK CLINotFoundError) is environmental —
    resolve_cli_path fail-opens to the bundled CLI, so "cannot launch" says
    nothing about the credential. Must not block readiness as dead."""
    from xyz_agent_context.agent_framework.providers.driver.drivers import (
        claude_oauth as mod,
    )
    from xyz_agent_context.agent_framework.adapters.claude import sdk as claude_sdk

    creds = tmp_path / ".credentials.json"
    creds.write_text("{}")
    monkeypatch.setattr(mod, "resolve_claude_credentials_path", lambda ref: creds)
    monkeypatch.setattr(
        claude_sdk, "_stage_claude_oauth_credentials", lambda config_dir: None
    )

    class CLINotFoundError(Exception):
        pass

    class _TextBlock:
        def __init__(self, text):
            self.text = text

    class _AssistantMessage:
        def __init__(self):
            self.content = []

    async def _raising_query(*, prompt, options):
        raise CLINotFoundError("claude binary not found")
        yield  # pragma: no cover — makes this an async generator

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=_AssistantMessage,
        TextBlock=_TextBlock,
        ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
        query=_raising_query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    verdict, msg = await ClaudeOAuthDriver(_claude_card()).verify_live()

    assert verdict == VERIFY_UNKNOWN
    assert "unavailable" in msg


# ---------------------------------------------------------------------------
# user_service.test_provider — oauth rows delegate; tri-state mapping
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_client():
    from xyz_agent_context.utils.db.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient
    from xyz_agent_context.utils.db.schema_registry import auto_migrate

    backend = SQLiteBackend(":memory:")
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    yield client
    await client.close()


async def _seed_oauth_row(db, *, protocol="openai", driver_type="codex_oauth"):
    await db.insert("user_providers", {
        "user_id": "user_x",
        "provider_id": "prov_oauth",
        "name": "CLI login",
        "source": "user",
        "protocol": protocol,
        "auth_type": "oauth",
        "api_key": "",
        "base_url": "",
        "models": '["gpt-5.1-codex"]',
        "driver_type": driver_type,
        "auth_ref": "codex-cli:~/.codex/auth.json",
    })


@pytest.mark.asyncio
async def test_oauth_row_delegates_to_driver_verify_live(db_client, monkeypatch):
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    calls = []

    async def fake_verify(self):
        calls.append(type(self).__name__)
        return VERIFY_DEAD, "access token could not be refreshed — run `codex login`"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", fake_verify)

    await _seed_oauth_row(db_client)
    ok, msg = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is False
    assert "codex login" in msg
    assert calls == ["CodexOAuthDriver"]


@pytest.mark.asyncio
async def test_oauth_row_without_driver_type_resolves_by_protocol(db_client, monkeypatch):
    """Legacy rows predate the driver_type column: openai → codex_oauth."""
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    calls = []

    async def fake_verify(self):
        calls.append(type(self).__name__)
        return VERIFY_OK, "live"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", fake_verify)

    await _seed_oauth_row(db_client, driver_type=None)
    ok, _ = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is True
    assert calls == ["CodexOAuthDriver"]


@pytest.mark.asyncio
async def test_oauth_row_never_passes_unconditionally(db_client, monkeypatch):
    """The literal regression: a dead credential must not test green."""
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    async def dead(self):
        return VERIFY_DEAD, "expired"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", dead)

    await _seed_oauth_row(db_client)
    ok, msg = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is False
    assert "managed by Claude Code CLI" not in msg


@pytest.mark.asyncio
async def test_oauth_unknown_verdict_does_not_block(db_client, monkeypatch):
    """Review item 4: "cannot verify here" must NOT read as "credential is
    dead" — a False here would permanently block ProviderReadiness's edge
    recovery (the only path that re-arms PAUSED_NO_QUOTA jobs)."""
    from xyz_agent_context.agent_framework.providers.user_service import (
        UserProviderService,
    )

    async def undecidable(self):
        return VERIFY_UNKNOWN, "cannot verify from the control plane"

    monkeypatch.setattr(CodexOAuthDriver, "verify_live", undecidable)

    await _seed_oauth_row(db_client)
    ok, msg = await UserProviderService(db_client).test_provider("user_x", "prov_oauth")

    assert ok is True
    assert "not live-verified" in msg


# ---------------------------------------------------------------------------
# registry.test_provider — transient path fails closed for oauth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_test_provider_fails_closed_for_oauth():
    from xyz_agent_context.agent_framework.providers.registry import (
        ProviderRegistry,
    )
    from xyz_agent_context.schema.provider_schema import (
        AuthType,
        ProviderConfig,
        ProviderProtocol,
        ProviderSource,
    )

    cfg = ProviderConfig(
        provider_id="_transient",
        name="oauth transient",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.OAUTH,
        api_key="",
        base_url="",
        models=[],
    )
    ok, msg = await ProviderRegistry().test_provider(cfg)

    assert ok is False
