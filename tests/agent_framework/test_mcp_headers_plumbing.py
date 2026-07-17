"""
@file_name: test_mcp_headers_plumbing.py
@author:
@date: 2026-07-15
@description: Tests for MCP custom-header plumbing across the framework
adapter layer and the executor wire protocol.

Covers:
- ClaudeAgentSDK spec → McpSSEServerConfig conversion (headers verbatim)
- CodexSDKv2 bearer extraction (Authorization: Bearer → env var + override)
- build_agent_loop_request carrying header-bearing specs across the
  orchestrator → executor boundary
- API-side header masking (values never leave the backend readable)
"""
from __future__ import annotations

from pathlib import Path

from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _build_claude_mcp_config,
)
from xyz_agent_context.agent_framework.xyz_codex_official_sdk import (
    _build_codex_config_overrides,
    codex_mcp_bearer_env,
)
from xyz_agent_context.agent_runtime.executor_protocol import (
    build_agent_loop_request,
)
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    OpenAIConfig,
    set_user_config,
)

from backend.routes.agents_mcps import _mask_header_value, _masked_headers

SPECS = {
    "chat_module": {"url": "http://localhost:7804/sse"},
    "web3": {
        "url": "http://frps.example.com:6027/sse",
        "headers": {"Authorization": "Bearer secret-token-1234567890"},
    },
}


# ---------------------------------------------------------------------------
# Claude adapter
# ---------------------------------------------------------------------------

def test_claude_mcp_config_passes_headers_verbatim():
    config = _build_claude_mcp_config(SPECS)
    assert config["web3"] == {
        "type": "sse",
        "url": "http://frps.example.com:6027/sse",
        "headers": {"Authorization": "Bearer secret-token-1234567890"},
    }


def test_claude_mcp_config_omits_headers_key_for_internal_servers():
    config = _build_claude_mcp_config(SPECS)
    assert config["chat_module"] == {
        "type": "sse",
        "url": "http://localhost:7804/sse",
    }
    assert "headers" not in config["chat_module"]


# ---------------------------------------------------------------------------
# Codex adapter (bearer-only support)
# ---------------------------------------------------------------------------

def test_codex_bearer_env_extracts_token():
    env = codex_mcp_bearer_env(SPECS)
    assert len(env) == 1
    (name, token), = env.items()
    assert name.startswith("NARRANEXUS_MCP_BEARER_WEB3_")
    assert token == "secret-token-1234567890"


def test_codex_bearer_env_names_do_not_collide_across_similar_server_names():
    """"shop-api" and "shop_api" sanitize to the same skeleton; without the
    hash suffix they would share one env var and A's token would be sent to
    B's endpoint."""
    specs = {
        "shop-api": {"url": "http://a/sse", "headers": {"Authorization": "Bearer tok-A"}},
        "shop_api": {"url": "http://b/sse", "headers": {"Authorization": "Bearer tok-B"}},
    }
    env = codex_mcp_bearer_env(specs)
    assert len(env) == 2
    assert set(env.values()) == {"tok-A", "tok-B"}


def test_codex_bearer_env_skips_non_bearer_headers():
    env = codex_mcp_bearer_env(
        {"custom": {"url": "http://x/sse", "headers": {"X-Api-Key": "k"}}}
    )
    assert env == {}


def test_codex_overrides_emit_bearer_token_env_var():
    result = _build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers=SPECS,
        permissions=None,
    )
    joined = "\n".join(result)
    assert 'mcp_servers.web3.bearer_token_env_var="NARRANEXUS_MCP_BEARER_WEB3_' in joined
    # The token value itself must NOT appear in config overrides (argv).
    assert "secret-token-1234567890" not in joined
    # Internal server gets a URL entry but no bearer var.
    assert "mcp_servers.chat_module.bearer_token_env_var" not in joined


# ---------------------------------------------------------------------------
# Executor wire protocol
# ---------------------------------------------------------------------------

def test_agent_loop_request_carries_mcp_specs_with_headers():
    set_user_config(claude=ClaudeConfig(api_key="k"), openai=OpenAIConfig())
    req = build_agent_loop_request(
        framework="claude_code",
        working_path="/ws/agent_x",
        messages=[{"role": "user", "content": "hi"}],
        mcp_servers=SPECS,
        extra_env=None,
    )
    assert req["mcp_servers"]["web3"]["headers"]["Authorization"].startswith("Bearer ")
    assert "headers" not in req["mcp_servers"]["chat_module"]


# ---------------------------------------------------------------------------
# API masking
# ---------------------------------------------------------------------------

def test_mask_header_value_keeps_scheme_only():
    masked = _mask_header_value("Bearer secret-token-1234567890")
    assert masked == "Bearer ****7890"
    assert "secret-token" not in masked


def test_mask_header_value_hides_prefix_of_schemeless_secrets():
    # "sk-live-…" has no auth scheme — its prefix IS the secret.
    masked = _mask_header_value("sk-live-abcdef0123456789")
    assert masked == "****6789"
    assert "sk-liv" not in masked


def test_mask_header_value_fully_masks_short_values():
    assert _mask_header_value("shorttoken") == "****"


def test_masked_headers_none_passthrough():
    assert _masked_headers(None) is None
    assert _masked_headers({}) is None
