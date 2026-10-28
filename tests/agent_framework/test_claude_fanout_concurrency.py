"""
@file_name: test_claude_fanout_concurrency.py
@date: 2026-09-09
@description: Bounded parallel tool execution for the Claude Code CLI on a
claude.ai subscription — the lever that caps sub-agent (Task) fan-out.

The CLI runs every concurrency-safe tool call of ONE assistant message in
parallel (parallel Read / Grep / MCP calls and every sub-agent launch alike),
bounded only by ``CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`` (default 10 inside
the binary). On a subscription each sub-agent is its own model loop against
a shared quota, and the CLI never retries a 429 for a subscriber (PR #379
handles the retry / preservation side), so an unbounded fan-out is a 429
storm. ``ClaudeConfig.to_cli_env`` — the platform's settings → CLAUDE_CODE_*
seam — now injects the cap from ``settings.claude_max_tool_use_concurrency``
for SUBSCRIPTION auth only; keyed auth keeps the CLI default (the CLI retries
its own 429s there, and the cap would only slow parallel reads down). This
is a CONCURRENCY limit, never an iteration / time ceiling on the loop
(binding rule #14): every call still runs, later.

The cap is applied twice on purpose: once in ``to_cli_env`` and again in
the driver AFTER the skill ``extra_env`` merge, so a skill cannot raise or
erase it (fail-closed, the same order as CLAUDE_CODE_ENABLE_TASKS). The env
name is a fact about the CLI binary and is checked there when it is present.

Each test goes red when the injection is removed: the assertions are on the
env the SDK options carry into the subprocess, driven through the real
adapter so the seam is exercised where production uses it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import narranexus_plugins.frameworks_claude_code.sdk as sdk_mod
from narranexus_plugins.frameworks_claude_code.sdk import ClaudeAgentSDK
from narranexus.platform.agent_framework.api_config import (
    CLI_MAX_TOOL_USE_CONCURRENCY_ENV,
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)
from narranexus.platform.agent_framework.providers import framework_binding
from narranexus.platform.schema.provider_schema import (
    SUBSCRIPTION_AUTH_TYPES,
    AuthType,
)
from narranexus.platform.settings import settings

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _configure(auth_type: str) -> None:
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type=auth_type),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )


async def _cli_env(auth_type: str, **kwargs) -> dict:
    _configure(auth_type)
    sdk = ClaudeAgentSDK(working_path="/tmp/ws-fanout")
    _ = [e async for e in sdk.agent_loop(
        [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "fan out"},
        ],
        {},
        **kwargs,
    )]
    (client,) = _StubClient.instances
    return client.options.env


def _cli_binary() -> Path | None:
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    path = Path(os.path.dirname(claude_agent_sdk.__file__)) / "_bundled" / "claude"
    return path if path.is_file() else None


def test_env_name_is_the_cli_knob():
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV == "CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY"


def test_env_name_matches_the_bundled_cli_binary():
    """The knob is a fact about the binary; a CLI rename would turn the cap
    into a silent no-op, so check the name there when the binary is present."""
    binary = _cli_binary()
    if binary is None:
        pytest.skip("claude-agent-sdk bundled binary not installed")
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV.encode() in binary.read_bytes()


def test_subscription_set_is_the_cli_definition():
    assert SUBSCRIPTION_AUTH_TYPES == frozenset({"oauth", "oauth_token"})
    assert SUBSCRIPTION_AUTH_TYPES == {AuthType.OAUTH.value, AuthType.OAUTH_TOKEN.value}
    assert "api_key" not in SUBSCRIPTION_AUTH_TYPES


def test_subscription_set_has_exactly_one_definition():
    """Every consumer keys on the same object: the binding rules re-export
    the schema-level constant, and the driver / api_config import it."""
    assert framework_binding.SUBSCRIPTION_AUTH_TYPES is SUBSCRIPTION_AUTH_TYPES
    assert sdk_mod.SUBSCRIPTION_AUTH_TYPES is SUBSCRIPTION_AUTH_TYPES
    from narranexus.platform.agent_framework import api_config

    assert api_config.SUBSCRIPTION_AUTH_TYPES is SUBSCRIPTION_AUTH_TYPES


# A hand-written ("oauth", "oauth_token") pair in either order, as a tuple,
# list or set literal. Only the enum-backed definition in provider_schema may
# spell the set; everything else imports SUBSCRIPTION_AUTH_TYPES.
_SUBSCRIPTION_LITERAL = re.compile(
    r"""[(\[{]\s*(['"])oauth(_token)?\1\s*,\s*(['"])oauth(?(2)|_token)\3\s*,?\s*[)\]}]"""
)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_sources():
    roots = [_REPO_ROOT / "src", _REPO_ROOT / "backend"]
    roots += sorted((_REPO_ROOT / "plugins").glob("*/src"))
    for root in roots:
        yield from root.rglob("*.py")


def test_the_literal_pattern_matches_both_spellings_and_nothing_else():
    assert _SUBSCRIPTION_LITERAL.search('x in ("oauth", "oauth_token")')
    assert _SUBSCRIPTION_LITERAL.search("x in {'oauth_token', 'oauth'}")
    assert not _SUBSCRIPTION_LITERAL.search('x in ("oauth", "api_key")')
    assert not _SUBSCRIPTION_LITERAL.search('x in ("oauth_token", "oauth_token")')


def test_no_consumer_spells_the_subscription_set_by_hand():
    """`SUBSCRIPTION_AUTH_TYPES` is THE definition only if nothing else spells
    it: a literal copy would not follow the next subscription transport and
    would silently decide the opposite way (native-Claude detection, the
    helper-fallback gate, billing path, alias resolution)."""
    offenders = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SUBSCRIPTION_LITERAL.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert offenders == [], offenders


def test_default_setting_is_a_bounded_positive_cap():
    assert isinstance(settings.claude_max_tool_use_concurrency, int)
    assert 0 < settings.claude_max_tool_use_concurrency < 10  # below the CLI's own 10


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", sorted(SUBSCRIPTION_AUTH_TYPES))
async def test_cap_is_injected_for_subscription_auth(monkeypatch, auth_type):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 3)
    env = await _cli_env(auth_type)
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == "3"


@pytest.mark.asyncio
async def test_default_cap_reaches_the_subprocess_env_for_a_subscription():
    env = await _cli_env("oauth_token")
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == str(
        settings.claude_max_tool_use_concurrency
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", ["api_key", "bearer"])
async def test_keyed_auth_keeps_the_cli_default(auth_type):
    """Allowed / passthrough case: the CLI retries its own 429s for keyed
    auth, so nothing is injected and parallel tool batches stay at 10."""
    env = await _cli_env(auth_type)
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_value", ["50", ""])
async def test_skill_env_cannot_raise_the_cap(monkeypatch, skill_value):
    """Fail-closed: the cap is re-applied AFTER the skill extra_env merge, so
    a skill env cannot raise it back to the CLI default or erase it."""
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 3)
    env = await _cli_env(
        "oauth_token",
        extra_env={CLI_MAX_TOOL_USE_CONCURRENCY_ENV: skill_value, "TAVILY_API_KEY": "t"},
    )
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == "3"
    assert env["TAVILY_API_KEY"] == "t"  # other skill env untouched


@pytest.mark.asyncio
async def test_skill_env_passes_through_for_keyed_auth(monkeypatch):
    """Allowed case: keyed auth is not the cap's business. The re-apply must
    not hard-set a value the platform never injects for that auth, so a
    skill's own value survives untouched."""
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 3)
    env = await _cli_env(
        "api_key", extra_env={CLI_MAX_TOOL_USE_CONCURRENCY_ENV: "8"}
    )
    assert env[CLI_MAX_TOOL_USE_CONCURRENCY_ENV] == "8"


@pytest.mark.asyncio
async def test_zero_means_cli_default_and_injects_nothing(monkeypatch):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", 0)
    env = await _cli_env("oauth_token")
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env


@pytest.mark.asyncio
async def test_negative_is_treated_as_cli_default(monkeypatch):
    monkeypatch.setattr(settings, "claude_max_tool_use_concurrency", -5)
    env = await _cli_env("oauth_token")
    assert CLI_MAX_TOOL_USE_CONCURRENCY_ENV not in env
