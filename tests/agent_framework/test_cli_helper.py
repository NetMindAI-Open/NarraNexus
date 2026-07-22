"""
@file_name: test_cli_helper.py
@date: 2026-07-07
@description: Tests for the CLI-backed helper LLM (subscription covers helper).

Covers: get_helper_sdk dispatch precedence (cli > anthropic > openai), the
CLI-helper driver configs, the resolver routing an OAuth helper slot to the
CLI helper, and CliHelperSDK.llm_function's shared parse/cost/wrap logic.
"""
from __future__ import annotations

import contextvars

import pytest
from pydantic import BaseModel

from xyz_agent_context.agent_framework import api_config as ac
from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.anthropic_helper_sdk import AnthropicHelperSDK
from xyz_agent_context.agent_framework.cli_helper_sdk import CliHelperSDK
from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK


def _run_isolated(fn):
    return contextvars.copy_context().run(fn)


# ---------------------------------------------------------------------------
# Dispatch precedence
# ---------------------------------------------------------------------------

def test_dispatch_cli_when_cli_helper_set():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code"))
        return type(get_helper_sdk())
    assert _run_isolated(check) is CliHelperSDK


def test_dispatch_cli_wins_over_anthropic():
    def check():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            anthropic_helper=AnthropicHelperConfig(api_key="k"),
            cli_helper=CliHelperConfig(framework="codex_cli"),
        )
        return type(get_helper_sdk())
    assert _run_isolated(check) is CliHelperSDK


def test_dispatch_anthropic_when_only_anthropic_set():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        anthropic_helper=AnthropicHelperConfig(api_key="k"))
        return type(get_helper_sdk())
    assert _run_isolated(check) is AnthropicHelperSDK


def test_dispatch_openai_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig())
        return type(get_helper_sdk())
    assert _run_isolated(check) is OpenAIAgentsSDK


# ---------------------------------------------------------------------------
# Model resolution defaults per framework
# ---------------------------------------------------------------------------

def test_resolve_model_claude_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code", model=""))
        return CliHelperSDK._resolve_model(None)
    assert _run_isolated(check) == "haiku"


def test_resolve_model_codex_default():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="codex_cli", model="default"))
        return CliHelperSDK._resolve_model(None)
    # A ChatGPT-account subscription rejects the API-key-only "-codex-mini" ids,
    # so the default must be a plain gpt-5.x model (verified live 2026-07-08).
    model = _run_isolated(check)
    assert model.startswith("gpt-5")
    assert "codex" not in model


def test_resolve_model_slot_wins_over_percall():
    def check():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code", model="sonnet"))
        # a per-call OpenAI-flavoured name is ignored; slot model wins
        return CliHelperSDK._resolve_model("gpt-5.4-mini")
    assert _run_isolated(check) == "sonnet"


# ---------------------------------------------------------------------------
# Driver → CliHelperConfig
# ---------------------------------------------------------------------------

def _card(**kw):
    from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard
    base = dict(
        provider_id="p1", user_id="u1", name="n", source="claude_oauth",
        protocol="anthropic", auth_type="oauth", api_key="", base_url="",
        models=[], driver_type="claude_oauth", auth_ref="claude-cli:~/.claude",
    )
    base.update(kw)
    return ProviderCard(**base)


def test_claude_oauth_driver_builds_cli_helper():
    from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )
    cfg = ClaudeOAuthDriver(_card()).build_cli_helper_config("haiku")
    assert isinstance(cfg, CliHelperConfig)
    assert cfg.framework == "claude_code"
    assert cfg.auth_type == "oauth"
    assert cfg.api_key == ""
    assert cfg.model == "haiku"


def test_codex_oauth_driver_builds_cli_helper():
    from xyz_agent_context.agent_framework.provider_driver.drivers.codex_oauth import (
        CodexOAuthDriver,
    )
    card = _card(source="codex_oauth", protocol="openai", driver_type="codex_oauth")
    cfg = CodexOAuthDriver(card).build_cli_helper_config("gpt-5.1-codex-mini")
    assert cfg.framework == "codex_cli"
    assert cfg.auth_type == "oauth"


# ---------------------------------------------------------------------------
# Resolver routes an OAuth helper slot to the CLI helper
# ---------------------------------------------------------------------------

def test_resolver_routes_oauth_helper_to_cli():
    from xyz_agent_context.agent_framework.provider_driver.resolver import (
        _resolve_slot_target,
    )
    method, key = _resolve_slot_target("helper_llm", "claude_code", _card())
    assert method == "build_cli_helper_config"
    assert key == "cli_helper"


def test_resolver_apikey_helper_still_openai():
    from xyz_agent_context.agent_framework.provider_driver.resolver import (
        _resolve_slot_target,
    )
    card = _card(source="user", protocol="openai", auth_type="api_key", api_key="sk-x")
    method, key = _resolve_slot_target("helper_llm", "claude_code", card)
    assert method == "build_openai_config"


# ---------------------------------------------------------------------------
# CliHelperSDK.llm_function shared parse / wrap logic (CLI call mocked)
# ---------------------------------------------------------------------------

class _Out(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_llm_function_structured(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return '{"answer": "hi"}', 10, 5

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)

    def call():
        set_user_config(ClaudeConfig(), OpenAIConfig(),
                        cli_helper=CliHelperConfig(framework="claude_code"))
    contextvars.copy_context().run(call)
    # set in THIS task too so cli_helper_config resolves during the call
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))

    res = await sdk.llm_function("inst", "in", output_type=_Out)
    assert res.final_output.answer == "hi"


@pytest.mark.asyncio
async def test_llm_function_no_schema_returns_text(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return "plain reply", 0, 0

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))
    res = await sdk.llm_function("inst", "in")
    assert res.final_output == "plain reply"


@pytest.mark.asyncio
async def test_llm_function_raises_when_no_json(monkeypatch):
    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return "sorry, I cannot", 0, 0

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="claude_code"))
    # After the repair-retry change, an always-unparseable reply raises only
    # once the bounded retries are exhausted (see test_helper_json_repair.py).
    with pytest.raises(ValueError, match="did not return schema-valid JSON"):
        await sdk.llm_function("inst", "in", output_type=_Out)


# ---------------------------------------------------------------------------
# Codex one-shot: event-shape parsing against the REAL codex_official
# translator output (output_transfer emits {"type":"raw_response_event",
# "data":{...}}). These drive _run_codex_oneshot directly by faking the
# driver — the _run_oneshot-mocking tests above never exercised it, which is
# how three shape bugs shipped: (1) instructions folded into the user message
# left instructions.md empty and the CLI exited on startup; (2) the loop read
# ev["raw_event"]/ev["usage"] instead of ev["data"], so no text accumulated;
# (3) a terminal response.error was ignored, surfacing as a misleading empty
# "could not extract JSON" instead of a classifiable credential failure.
# ---------------------------------------------------------------------------

class _FakeCodexDriver:
    def __init__(self, events, captured):
        self._events = events
        self._captured = captured

    async def agent_loop(self, messages, mcp_servers):
        self._captured["messages"] = messages
        self._captured["mcp_servers"] = mcp_servers
        # Capture the ambient codex_config the driver would actually read — the
        # helper must install its OWN slot model + creds here, not the agent's.
        from xyz_agent_context.agent_framework.api_config import codex_config
        self._captured["codex_model"] = codex_config.model
        self._captured["codex_auth_ref"] = codex_config.auth_ref
        for ev in self._events:
            yield ev


def _use_codex_driver(monkeypatch, events, captured):
    import xyz_agent_context.agent_framework as af
    monkeypatch.setattr(
        af, "get_agent_loop_driver",
        lambda framework, **_kw: _FakeCodexDriver(events, captured),
    )
    set_user_config(ClaudeConfig(), OpenAIConfig(),
                    cli_helper=CliHelperConfig(framework="codex_cli", model="m"))


def _delta(d):
    return {"type": "raw_response_event", "data": {"type": "response.text.delta", "delta": d}}


@pytest.mark.asyncio
async def test_codex_oneshot_accumulates_text_and_usage(monkeypatch):
    """Reads assistant text from data.type==response.text.delta and usage from
    response.done — the shapes the codex_official translator actually emits."""
    captured = {}
    events = [
        {"type": "raw_response_event", "data": {"type": "response.text.delta"}},  # no delta key
        _delta('{"answer":'),
        _delta(' "hi"}'),
        {"type": "run_item_stream_event", "item": {"type": "thinking_item", "content": "noise"}},
        {"type": "raw_response_event",
         "data": {"type": "response.done", "usage": {"input_tokens": 11, "output_tokens": 7}}},
    ]
    _use_codex_driver(monkeypatch, events, captured)
    sdk = CliHelperSDK()
    res = await sdk.llm_function("inst", "in", output_type=_Out)
    assert res.final_output.answer == "hi"


@pytest.mark.asyncio
async def test_codex_oneshot_passes_instructions_as_system_message(monkeypatch):
    """The schema/instructions MUST ride a role=='system' message — the codex
    driver builds instructions.md ONLY from system messages; folding them into
    the user turn leaves it empty and the CLI refuses to start."""
    captured = {}
    _use_codex_driver(monkeypatch, [_delta('{"answer": "ok"}')], captured)
    sdk = CliHelperSDK()
    await sdk.llm_function("MY-INSTRUCTIONS", "the-user-input", output_type=_Out)
    roles = [m["role"] for m in captured["messages"]]
    assert "system" in roles, roles
    sys_msg = next(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "MY-INSTRUCTIONS" in sys_msg
    # user_input stays in its own user turn, NOT merged into the system prompt
    user_msg = next(m["content"] for m in captured["messages"] if m["role"] == "user")
    assert user_msg == "the-user-input"
    assert "the-user-input" not in sys_msg


@pytest.mark.asyncio
async def test_codex_oneshot_raises_classifiable_error_on_response_error(monkeypatch):
    """A terminal response.error (e.g. expired OAuth token) must raise an error
    that is_credential_error can classify — not silently return empty text."""
    from xyz_agent_context.agent_framework.llm_failure import is_credential_error

    captured = {}
    events = [{
        "type": "raw_response_event",
        "data": {
            "type": "response.error",
            "error_message": "Your access token could not be refreshed. Sign in again.",
            "error_type": "unauthorized",
        },
    }]
    _use_codex_driver(monkeypatch, events, captured)
    sdk = CliHelperSDK()
    with pytest.raises(RuntimeError) as exc:
        await sdk.llm_function("inst", "in", output_type=_Out)
    # carries the error type so downstream credential-alerting (#68) triggers
    assert "unauthorized" in str(exc.value)
    assert is_credential_error(str(exc.value)) is True


@pytest.mark.asyncio
async def test_codex_oneshot_installs_helper_model_and_creds(monkeypatch):
    """The codex driver reads model/creds from the ambient ``codex_config``. The
    helper must install its OWN slot model + OAuth ref there — NOT run on the
    agent slot's config (which is the flagship when agent==codex, and empty /
    credential-less when agent==claude). Regression for the review finding that
    _run_codex_oneshot ignored cli_helper_config entirely."""
    from xyz_agent_context.agent_framework.api_config import (
        CodexConfig,
        codex_config,
    )
    from xyz_agent_context.agent_framework.provider_driver.derive import (
        CODEX_CLI_CREDENTIALS_REF,
    )
    import xyz_agent_context.agent_framework as af

    captured = {}
    monkeypatch.setattr(
        af, "get_agent_loop_driver",
        lambda framework, **_kw: _FakeCodexDriver([_delta('{"answer": "ok"}')], captured),
    )
    # Ambient AGENT codex_config: a DIFFERENT (flagship) model + a stale ref —
    # the helper must not inherit either.
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        codex=CodexConfig(model="gpt-5.5", auth_type="oauth", auth_ref="stale-agent-ref"),
        cli_helper=CliHelperConfig(
            framework="codex_cli", model="gpt-5.4-mini", auth_type="oauth",
        ),
    )
    res = await CliHelperSDK().llm_function("inst", "in", output_type=_Out)
    assert res.final_output.answer == "ok"
    # Driver saw the HELPER's slot model + the canonical codex OAuth ref.
    assert captured["codex_model"] == "gpt-5.4-mini"
    assert captured["codex_auth_ref"] == CODEX_CLI_CREDENTIALS_REF
    # The ambient agent codex_config is restored afterwards (reset in finally).
    assert codex_config.model == "gpt-5.5"


# ---------------------------------------------------------------------------
# Probe: macOS Keychain fallback (file missing but Keychain has the token)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_ok_via_keychain_when_file_missing(monkeypatch, tmp_path):
    from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )
    missing = tmp_path / "nope" / ".credentials.json"
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(missing))

    async def _kc_yes():
        return True

    driver = ClaudeOAuthDriver(_card())
    monkeypatch.setattr(driver, "_keychain_has_credentials", _kc_yes)
    health = await driver.probe()
    assert health.ok is True
    assert "Keychain" in health.detail


@pytest.mark.asyncio
async def test_probe_fails_when_neither_file_nor_keychain(monkeypatch, tmp_path):
    from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
        ClaudeOAuthDriver,
    )
    missing = tmp_path / "nope" / ".credentials.json"
    monkeypatch.setenv("CLAUDE_CLI_CREDENTIALS_PATH", str(missing))

    async def _kc_no():
        return False

    driver = ClaudeOAuthDriver(_card())
    monkeypatch.setattr(driver, "_keychain_has_credentials", _kc_no)
    health = await driver.probe()
    assert health.ok is False
    assert "not found" in health.detail
