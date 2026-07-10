"""
@file_name: test_one_key_onboarding.py
@date: 2026-06-10
@description: Tests for the one-key onboarding feature:

1. get_helper_sdk factory dispatch (OpenAI vs Anthropic helper).
2. AnthropicHelperSDK structured output + streaming (stubbed client).
3. helper_llm slot accepts anthropic-protocol providers.
4. Driver resolver routes an anthropic helper provider to
   RuntimeLLMConfigs.anthropic_helper.
5. UserProviderService.onboard_one_key wires framework + provider +
   both slots from a single key, for both key types.
"""
from __future__ import annotations

import contextvars

import pytest
from pydantic import BaseModel

from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.agent_framework.anthropic_helper_sdk import AnthropicHelperSDK
from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from xyz_agent_context.agent_framework.provider_driver import (
    resolve_user_runtime_llm_configs,
)
from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


# =============================================================================
# Shared fakes
# =============================================================================

class _FakeDB:
    """Tiny in-memory user_providers / user_slots stand-in."""

    def __init__(self):
        self.providers: dict[str, dict] = {}
        self.slots: dict[tuple, dict] = {}

    async def get(self, table, filters=None):
        filters = filters or {}
        if table == "user_providers":
            rows = self.providers.values()
        elif table == "user_slots":
            rows = self.slots.values()
        else:
            return []
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
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        return 0


def _run_isolated(fn, *args, **kwargs):
    """Run fn in a copied contextvars context so ContextVar writes
    (set_user_config) don't leak into other tests."""
    ctx = contextvars.copy_context()
    return ctx.run(fn, *args, **kwargs)


@pytest.fixture(autouse=True)
def _stub_key_probe(monkeypatch):
    """onboard_one_key live-probes the key via provider_registry.
    Stub it for every test in this file so nothing touches the network;
    individual tests override the stub to exercise failure outcomes."""
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    async def _ok(provider):
        return True, "Connected successfully"

    monkeypatch.setattr(provider_registry, "test_provider", _ok)


# =============================================================================
# 1. Factory dispatch
# =============================================================================

@pytest.mark.asyncio
async def test_factory_returns_openai_sdk_by_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig())
        return type(get_helper_sdk())
    assert _run_isolated(check) is OpenAIAgentsSDK


@pytest.mark.asyncio
async def test_factory_returns_anthropic_sdk_when_ctx_set():
    def check():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            None, AnthropicHelperConfig(api_key="k"),
        )
        return type(get_helper_sdk())
    assert _run_isolated(check) is AnthropicHelperSDK


@pytest.mark.asyncio
async def test_factory_resets_when_config_set_without_anthropic():
    """A later set_user_config WITHOUT anthropic_helper must clear the
    dispatch — stale anthropic config from a previous turn would
    otherwise leak into the next user's task."""
    def check():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            None, AnthropicHelperConfig(api_key="k"),
        )
        set_user_config(ClaudeConfig(), OpenAIConfig())
        return type(get_helper_sdk())
    assert _run_isolated(check) is OpenAIAgentsSDK


# =============================================================================
# 2. AnthropicHelperSDK behaviour (stubbed AsyncAnthropic)
# =============================================================================

class _Out(BaseModel):
    answer: str
    score: int


class _StubTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _StubUsage:
    def __init__(self, inp=10, out=5):
        self.input_tokens = inp
        self.output_tokens = out


class _StubResponse:
    def __init__(self, text):
        self.content = [_StubTextBlock(text)]
        self.usage = _StubUsage()


class _StubMessages:
    def __init__(self, text):
        self._text = text

    async def create(self, **kwargs):
        return _StubResponse(self._text)

    def stream(self, **kwargs):
        text = self._text

        class _StreamCM:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            @property
            def text_stream(self):
                async def _gen():
                    for chunk in (text[:3], text[3:]):
                        if chunk:
                            yield chunk
                return _gen()

            async def get_final_message(self):
                return _StubResponse(text)

        return _StreamCM()


class _StubAnthropicClient:
    def __init__(self, text):
        self.messages = _StubMessages(text)


@pytest.mark.asyncio
async def test_anthropic_helper_structured_output(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client",
        lambda: _StubAnthropicClient('{"answer": "ok", "score": 7}'),
    )
    result = await sdk.llm_function(
        instructions="extract", user_input="text", output_type=_Out,
    )
    assert isinstance(result.final_output, _Out)
    assert result.final_output.answer == "ok"
    assert result.final_output.score == 7
    assert result.raw_text == '{"answer": "ok", "score": 7}'


@pytest.mark.asyncio
async def test_anthropic_helper_structured_strips_code_fences(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client",
        lambda: _StubAnthropicClient('```json\n{"answer": "x", "score": 1}\n```'),
    )
    result = await sdk.llm_function(
        instructions="extract", user_input="text", output_type=_Out,
    )
    assert result.final_output.answer == "x"


@pytest.mark.asyncio
async def test_anthropic_helper_plain_text(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client", lambda: _StubAnthropicClient("hello there"),
    )
    result = await sdk.llm_function(instructions="reply", user_input="hi")
    assert result.final_output == "hello there"


@pytest.mark.asyncio
async def test_anthropic_helper_malformed_json_raises(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client", lambda: _StubAnthropicClient("not json at all"),
    )
    with pytest.raises(ValueError, match="Could not extract JSON"):
        await sdk.llm_function(
            instructions="extract", user_input="text", output_type=_Out,
        )


@pytest.mark.asyncio
async def test_anthropic_helper_stream_yields_deltas(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client", lambda: _StubAnthropicClient("streamed text"),
    )
    chunks = [d async for d in sdk.llm_stream(instructions="r", user_input="u")]
    assert "".join(chunks) == "streamed text"
    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_anthropic_helper_reasoning_effort_is_clamped_not_raised(monkeypatch):
    """Iron rule #15: unsupported params are clamped with a log, never an
    error — narrative call sites pass reasoning_effort unconditionally."""
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    sdk = AnthropicHelperSDK()
    monkeypatch.setattr(
        sdk, "_build_client", lambda: _StubAnthropicClient("fine"),
    )
    result = await sdk.llm_function(
        instructions="r", user_input="u", reasoning_effort="high",
    )
    assert result.final_output == "fine"


@pytest.mark.asyncio
async def test_anthropic_helper_ignores_openai_flavored_model_override(monkeypatch):
    """Per-call model= overrides carry OpenAI names (narrative judge) —
    the anthropic helper must ignore them and use the slot model."""
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        None, AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    assert AnthropicHelperSDK._resolve_model("gpt-5.4-mini") == "claude-haiku-4-5"


# =============================================================================
# 3. helper_llm slot accepts anthropic providers
# =============================================================================

@pytest.mark.asyncio
async def test_set_slot_helper_accepts_anthropic_provider():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant-x",
    )
    config = await svc.set_slot("u1", "helper_llm", new_ids[0], "claude-haiku-4-5")
    assert config.slots["helper_llm"].provider_id == new_ids[0]


@pytest.mark.asyncio
async def test_set_slot_helper_accepts_oauth_providers():
    """OAuth rows (claude_oauth / codex_oauth) now serve the helper slot via
    the CLI helper (subscription covers both slots, 2026-07). set_slot must
    accept them — the resolver routes them to a CliHelperConfig."""
    db = _FakeDB()
    svc = UserProviderService(db)
    _, claude_ids = await svc.add_provider(user_id="u1", card_type="claude_oauth")

    # Re-assigning an OAuth provider to the helper slot must NOT raise.
    cfg = await svc.set_slot("u1", "helper_llm", claude_ids[0], "haiku")
    assert cfg.slots["helper_llm"].provider_id == claude_ids[0]


@pytest.mark.asyncio
async def test_set_slot_helper_still_accepts_openai_provider():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(
        user_id="u1", card_type="openai", api_key="sk-x",
    )
    config = await svc.set_slot("u1", "helper_llm", new_ids[0], "gpt-5.4-mini")
    assert config.slots["helper_llm"].provider_id == new_ids[0]


# =============================================================================
# 4. Resolver routes anthropic helper to RuntimeLLMConfigs.anthropic_helper
# =============================================================================

@pytest.mark.asyncio
async def test_resolver_builds_anthropic_helper_config():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant-x",
    )
    pid = new_ids[0]
    await svc.set_slot("u1", "agent", pid, "claude-opus-4-8")
    await svc.set_slot("u1", "helper_llm", pid, "claude-haiku-4-5")

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.anthropic_helper is not None
    assert cfg.anthropic_helper.api_key == "sk-ant-x"
    assert cfg.anthropic_helper.model == "claude-haiku-4-5"
    assert cfg.openai == OpenAIConfig()          # unused empty default
    assert cfg.claude.api_key == "sk-ant-x"      # agent on the same key


@pytest.mark.asyncio
async def test_resolver_keeps_openai_helper_path_unchanged():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, anth_ids = await svc.add_provider(
        user_id="u1", card_type="anthropic", api_key="sk-ant-x",
    )
    _, oai_ids = await svc.add_provider(
        user_id="u1", card_type="openai", api_key="sk-oai",
    )
    await svc.set_slot("u1", "agent", anth_ids[0], "claude-opus-4-8")
    await svc.set_slot("u1", "helper_llm", oai_ids[0], "gpt-5.4-mini")

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.anthropic_helper is None
    assert cfg.openai.api_key == "sk-oai"
    assert cfg.openai.model == "gpt-5.4-mini"


# =============================================================================
# 5. onboard_one_key orchestration
# =============================================================================

@pytest.mark.asyncio
async def test_onboard_anthropic_key_wires_everything():
    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids, meta = await svc.onboard_one_key("u1", "sk-ant-abc123")

    assert meta["provider_type"] == "anthropic"
    assert meta["agent_framework"] == "claude_code"
    assert meta["agent_model"] == "claude-opus-4-8"
    assert meta["helper_model"] == "claude-haiku-4-5"

    pid = new_ids[0]
    assert config.slots["agent"].provider_id == pid
    assert config.slots["agent"].model == "claude-opus-4-8"
    assert config.slots["helper_llm"].provider_id == pid
    assert config.slots["helper_llm"].model == "claude-haiku-4-5"
    assert await svc.get_user_agent_framework("u1") == "claude_code"
    assert not await svc.validate_slots("u1")    # all slots configured


@pytest.mark.asyncio
async def test_onboard_openai_key_wires_codex_framework():
    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids, meta = await svc.onboard_one_key("u1", "sk-proj-abc123")

    assert meta["provider_type"] == "openai"
    assert meta["agent_framework"] == "codex_cli"
    assert meta["agent_model"] == "gpt-5.5"
    assert meta["helper_model"] == "gpt-5.4-mini"

    pid = new_ids[0]
    assert db.providers[pid]["protocol"] == "openai"
    assert db.providers[pid]["source"] == "user"   # codex_cli allows source=user
    assert config.slots["agent"].provider_id == pid
    assert config.slots["helper_llm"].provider_id == pid
    assert await svc.get_user_agent_framework("u1") == "codex_cli"


@pytest.mark.asyncio
async def test_onboard_netmind_routes_each_slot_to_its_protocol_row():
    """The netmind card creates TWO linked providers (anthropic +
    openai); the agent slot must land on the anthropic row and the
    helper on the openai row."""
    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids, meta = await svc.onboard_one_key(
        "u1", "netmind-key", provider_type="netmind",
    )

    assert meta["agent_framework"] == "claude_code"
    assert meta["agent_model"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert meta["helper_model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert len(new_ids) == 2

    agent_pid = config.slots["agent"].provider_id
    helper_pid = config.slots["helper_llm"].provider_id
    assert agent_pid != helper_pid
    assert db.providers[agent_pid]["protocol"] == "anthropic"
    assert db.providers[helper_pid]["protocol"] == "openai"


@pytest.mark.asyncio
async def test_onboard_netmind_resolves_end_to_end():
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "netmind-key", provider_type="netmind")

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.claude.api_key == "netmind-key"
    assert cfg.claude.model == "deepseek-ai/DeepSeek-V4-Pro"
    assert cfg.openai.api_key == "netmind-key"
    assert cfg.openai.model == "deepseek-ai/DeepSeek-V4-Flash"
    assert cfg.anthropic_helper is None    # helper rides the openai row


@pytest.mark.asyncio
async def test_onboard_yunwu_and_openrouter_accepted():
    for source in ("yunwu", "openrouter"):
        db = _FakeDB()
        svc = UserProviderService(db)
        config, new_ids, meta = await svc.onboard_one_key(
            "u1", "agg-key", provider_type=source,
        )
        assert meta["agent_framework"] == "claude_code"
        assert meta["agent_model"] == "claude-opus-4-8"
        assert meta["helper_model"] == "gpt-5.4-mini"
        assert len(new_ids) == 2
        assert db.providers[config.slots["agent"].provider_id]["protocol"] == "anthropic"
        assert db.providers[config.slots["helper_llm"].provider_id]["protocol"] == "openai"


@pytest.mark.asyncio
async def test_onboard_explicit_type_overrides_prefix():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, _, meta = await svc.onboard_one_key(
        "u1", "sk-ant-looks-anthropic", provider_type="openai",
    )
    assert meta["provider_type"] == "openai"
    assert meta["agent_framework"] == "codex_cli"


@pytest.mark.asyncio
async def test_onboard_rejects_invalid_key_before_writing(monkeypatch):
    """A definitively rejected key (401/403) must fail the onboard AND
    leave the config untouched — no provider row, no slots."""
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    async def _auth_fail(provider):
        return False, "Authentication failed (invalid API key)"
    monkeypatch.setattr(provider_registry, "test_provider", _auth_fail)

    db = _FakeDB()
    svc = UserProviderService(db)
    with pytest.raises(ValueError, match="API key rejected"):
        await svc.onboard_one_key("u1", "sk-ant-typo")

    assert db.providers == {}
    assert db.slots == {}


@pytest.mark.asyncio
async def test_onboard_proceeds_unverified_on_transient_probe_failure(monkeypatch):
    """Network/5xx probe failures must NOT block a (possibly good) key —
    proceed and report key_check='unverified (...)'."""
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    async def _net_fail(provider):
        return False, "Connection failed: timeout"
    monkeypatch.setattr(provider_registry, "test_provider", _net_fail)

    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids, meta = await svc.onboard_one_key("u1", "sk-ant-good")

    assert meta["key_check"].startswith("unverified")
    assert config.slots["agent"].provider_id == new_ids[0]


@pytest.mark.asyncio
async def test_onboard_reports_key_check_ok():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, _, meta = await svc.onboard_one_key("u1", "sk-ant-good")
    assert meta["key_check"] == "ok"


@pytest.mark.asyncio
async def test_onboard_empty_key_rejected():
    db = _FakeDB()
    svc = UserProviderService(db)
    with pytest.raises(ValueError, match="api_key is required"):
        await svc.onboard_one_key("u1", "   ")


@pytest.mark.asyncio
async def test_onboard_bad_provider_type_rejected():
    db = _FakeDB()
    svc = UserProviderService(db)
    with pytest.raises(ValueError, match="provider_type must be"):
        await svc.onboard_one_key("u1", "sk-x", provider_type="gemini")


@pytest.mark.asyncio
async def test_onboard_then_resolve_end_to_end_claude_key():
    """The full chain: one Claude key → onboard → resolver produces a
    runnable RuntimeLLMConfigs with the anthropic helper installed."""
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "sk-ant-e2e")

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.claude.api_key == "sk-ant-e2e"
    assert cfg.claude.model == "claude-opus-4-8"
    assert cfg.anthropic_helper is not None
    assert cfg.anthropic_helper.model == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_onboard_then_resolve_end_to_end_openai_key():
    """One OpenAI key → onboard → resolver produces a codex agent config
    plus the standard OpenAI helper."""
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "sk-proj-e2e")

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.codex.api_key == "sk-proj-e2e"
    assert cfg.codex.model == "gpt-5.5"
    assert cfg.openai.api_key == "sk-proj-e2e"
    assert cfg.openai.model == "gpt-5.4-mini"
    assert cfg.anthropic_helper is None


# =============================================================================
# 6. Reasoning params reach both agent frameworks through the resolver
# =============================================================================

@pytest.mark.asyncio
async def test_resolver_threads_reasoning_params_into_codex():
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "sk-proj-x")
    pid = (await svc.get_user_config("u1")).slots["agent"].provider_id
    await svc.set_slot(
        "u1", "agent", pid, "gpt-5.5",
        thinking="on", reasoning_effort="high",
    )

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.codex.reasoning_effort == "high"
    assert cfg.codex.thinking == "on"


@pytest.mark.asyncio
async def test_resolver_threads_reasoning_params_into_claude():
    """Previously only the legacy fallback honored the slot's neutral
    params; the driver path must thread them too."""
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "sk-ant-x")
    pid = (await svc.get_user_config("u1")).slots["agent"].provider_id
    await svc.set_slot(
        "u1", "agent", pid, "claude-opus-4-8",
        thinking="off", reasoning_effort="max",
    )

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.claude.thinking == "off"
    assert cfg.claude.reasoning_effort == "max"


def test_codex_toml_maps_reasoning_effort():
    from pathlib import Path
    from xyz_agent_context.agent_framework.api_config import CodexConfig
    from xyz_agent_context.agent_framework._codex_config_toml_builder import (
        build_codex_config_toml,
    )

    toml = build_codex_config_toml(
        instructions_path=Path("/tmp/instructions.md"),
        mcp_server_urls={},
        config=CodexConfig(model="gpt-5.5", reasoning_effort="high"),
        permissions={},
    )
    assert 'model_reasoning_effort = "high"' in toml


def test_codex_toml_clamps_max_to_high():
    from pathlib import Path
    from xyz_agent_context.agent_framework.api_config import CodexConfig
    from xyz_agent_context.agent_framework._codex_config_toml_builder import (
        build_codex_config_toml,
    )

    toml = build_codex_config_toml(
        instructions_path=Path("/tmp/instructions.md"),
        mcp_server_urls={},
        config=CodexConfig(model="gpt-5.5", reasoning_effort="max"),
        permissions={},
    )
    assert 'model_reasoning_effort = "high"' in toml


def test_codex_toml_auto_emits_no_effort_key():
    from pathlib import Path
    from xyz_agent_context.agent_framework.api_config import CodexConfig
    from xyz_agent_context.agent_framework._codex_config_toml_builder import (
        build_codex_config_toml,
    )

    toml = build_codex_config_toml(
        instructions_path=Path("/tmp/instructions.md"),
        mcp_server_urls={},
        config=CodexConfig(model="gpt-5.5"),
        permissions={},
    )
    assert "model_reasoning_effort" not in toml


# =============================================================================
# 7. Framework auth probe recognises the API-key leg
# =============================================================================

@pytest.mark.asyncio
async def test_framework_probe_passes_on_api_key_provider(monkeypatch):
    """An OpenAI-key onboarded user (codex_cli + api-key provider) must
    NOT be told 'auth missing, run codex login' — the API key IS the
    auth. Same for claude_code with an anthropic key."""
    from backend.routes.providers import _probe_agent_framework_auth
    from xyz_agent_context.utils import db_factory

    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.onboard_one_key("u1", "sk-proj-x")          # codex_cli path
    await svc.onboard_one_key("u2", "sk-ant-x")           # claude_code path

    async def _fake_db():
        return db
    monkeypatch.setattr(db_factory, "get_db_client", _fake_db)

    probe = await _probe_agent_framework_auth("codex_cli", user_id="u1")
    assert probe["ok"] is True
    assert "API-key provider" in probe["detail"]

    probe = await _probe_agent_framework_auth("claude_code", user_id="u2")
    assert probe["ok"] is True
    assert "API-key provider" in probe["detail"]


# ---------------------------------------------------------------------------
# Phase 5 follow-up: netmind inference base is env-configurable, but ONLY on
# the use-subscription (minted-key) path. Manual paste keeps the prod default.
# ---------------------------------------------------------------------------

def test_build_dual_providers_netmind_default_is_prod():
    """No inference_base → the hardcoded prod bases (manual-paste path)."""
    from xyz_agent_context.agent_framework.user_provider_service import (
        _build_dual_providers,
    )
    rows = {r["protocol"]: r for r in _build_dual_providers("netmind", "k", "g")}
    assert rows["anthropic"]["base_url"] == "https://api.netmind.ai/inference-api/anthropic"
    assert rows["openai"]["base_url"] == "https://api.netmind.ai/inference-api/openai/v1"


def test_build_dual_providers_netmind_inference_base_override():
    """use-subscription passes a base → both rows point at that env (dev)."""
    from xyz_agent_context.agent_framework.user_provider_service import (
        _build_dual_providers,
    )
    rows = {
        r["protocol"]: r
        for r in _build_dual_providers(
            "netmind", "k", "g",
            inference_base="https://test.api.netmind.ai/inference-api",
        )
    }
    assert rows["anthropic"]["base_url"] == "https://test.api.netmind.ai/inference-api/anthropic"
    assert rows["openai"]["base_url"] == "https://test.api.netmind.ai/inference-api/openai/v1"


def test_build_dual_providers_trailing_slash_normalized():
    from xyz_agent_context.agent_framework.user_provider_service import (
        _build_dual_providers,
    )
    rows = {
        r["protocol"]: r
        for r in _build_dual_providers(
            "netmind", "k", "g",
            inference_base="https://test.api.netmind.ai/inference-api/",  # trailing /
        )
    }
    assert rows["openai"]["base_url"] == "https://test.api.netmind.ai/inference-api/openai/v1"


def test_inference_base_override_only_applies_to_netmind():
    """A stray inference_base must NOT rewrite yunwu/openrouter bases."""
    from xyz_agent_context.agent_framework.user_provider_service import (
        _build_dual_providers,
    )
    rows = {
        r["protocol"]: r
        for r in _build_dual_providers(
            "yunwu", "k", "g",
            inference_base="https://test.api.netmind.ai/inference-api",
        )
    }
    assert "netmind" not in rows["openai"]["base_url"]
    assert rows["openai"]["base_url"] == "https://api.yunwuai.cloud/v1"
