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

from xyz_agent_context.agent_framework.adapters.claude.sdk import (
    _build_claude_mcp_config,
)
from xyz_agent_context.agent_framework.adapters.codex.official_sdk import (
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

from backend.routes.agents.mcps import _mask_header_value, _masked_headers

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


def test_claude_mcp_config_omits_headers_key_when_spec_has_none():
    """Adapter contract only. NOTE: since 2026-08-01 module ("internal")
    servers DO arrive with headers — context_runtime injects the caller
    identity — so this fixture's bare ``chat_module`` represents "a spec
    without headers", not "an internal server"."""
    config = _build_claude_mcp_config(SPECS)
    assert config["chat_module"] == {
        "type": "sse",
        "url": "http://localhost:7804/sse",
    }
    assert "headers" not in config["chat_module"]


def test_claude_mcp_config_is_sorted_by_server_name_regardless_of_input_order():
    """R4c tool-order determinism: the config dict is serialized into the
    CLI's MCP config, so its key order must not depend on upstream insertion
    order (active_instances iteration + pass_mcp_servers merge)."""
    shuffled = {
        "zeta": {"url": "http://z/sse"},
        "alpha": {"url": "http://a/sse"},
        "mid": {"url": "http://m/sse"},
    }
    config = _build_claude_mcp_config(shuffled)
    assert list(config.keys()) == ["alpha", "mid", "zeta"]
    # Same input in a different insertion order -> byte-identical result.
    reordered = {k: shuffled[k] for k in ["mid", "zeta", "alpha"]}
    assert list(_build_claude_mcp_config(reordered).keys()) == ["alpha", "mid", "zeta"]


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


# ---------------------------------------------------------------------------
# Caller-identity headers (P1 evt_0dcee899) must survive both adapters
# ---------------------------------------------------------------------------


def test_claude_adapter_forwards_the_caller_identity_headers():
    """The middle link of the identity chain: context_runtime injects the
    headers, and the claude adapter must hand them to the CLI verbatim —
    otherwise the module MCP server never learns who is calling and the fix
    silently degrades to the old "trust the model's agent_id" behaviour."""
    from xyz_agent_context.module._mcp_identity import (
        AGENT_ID_HEADER,
        agent_id_headers,
    )

    agent = "agent_d8795abf5021"
    specs = {"social_network_module": {
        "url": "http://localhost:7802/sse",
        "headers": agent_id_headers(agent),
    }}

    entry = _build_claude_mcp_config(specs)["social_network_module"]
    assert entry["headers"][AGENT_ID_HEADER] == agent


def test_codex_adapter_transmits_identity_via_the_borrowed_bearer():
    """Codex cannot carry arbitrary headers, which is exactly why identity
    also rides an Authorization bearer. Assert that channel survives, or the
    fix would work on claude and silently do nothing on codex."""
    from xyz_agent_context.module._mcp_identity import (
        BEARER_AGENT_PREFIX,
        agent_id_headers,
    )

    agent = "agent_d8795abf5021"
    specs = {"social_network_module": {
        "url": "http://localhost:7802/sse",
        "headers": agent_id_headers(agent),
    }}

    env = codex_mcp_bearer_env(specs)
    assert env, "codex must expose the identity bearer as an env var"
    token = next(iter(env.values()))
    assert token == f"{BEARER_AGENT_PREFIX}{agent}"

    # ...and the config override that makes codex actually send it.
    joined = "\n".join(_build_codex_config_overrides(
        instructions_path=Path("/tmp/i.md"),
        mcp_servers=specs,
        permissions=None,
    ))
    assert "mcp_servers.social_network_module.bearer_token_env_var=" in joined
    # The identity value must not leak into argv.
    assert agent not in joined


def _captured_warnings(fn):
    """Run ``fn`` and return the loguru WARNING text it emitted.

    loguru does not propagate to stdlib logging, so pytest's ``caplog`` sees
    nothing — an assertion like ``"x" not in caplog.text`` would pass on an
    always-empty string. Add a real sink instead.
    """
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        fn()
    finally:
        logger.remove(sink_id)
    return "\n".join(lines)


def test_identity_header_does_not_warn_on_codex():
    """The identity header is dual-sent because codex cannot carry it, so its
    drop is expected and must not warn — otherwise every codex turn logs one
    line per module server (~16/turn) and buries the warnings that matter
    (a USER's custom header silently vanishing)."""
    from xyz_agent_context.module._mcp_identity import agent_id_headers

    specs = {"social_network_module": {
        "url": "http://localhost:7802/sse",
        "headers": agent_id_headers("agent_d8795abf5021"),
    }}
    captured = {}
    text = _captured_warnings(lambda: captured.update(env=codex_mcp_bearer_env(specs)))

    assert captured["env"], "the bearer must still be extracted"
    assert "not supported by codex" not in text


def test_a_users_own_custom_header_still_warns():
    """The exemption must be narrow: a real custom header vanishing is
    exactly what that warning exists for."""
    specs = {"shop": {
        "url": "http://x/sse",
        "headers": {"X-Api-Key": "k", "Authorization": "Bearer tok"},
    }}
    text = _captured_warnings(lambda: codex_mcp_bearer_env(specs))

    assert "X-Api-Key" in text
    assert "not supported by codex" in text


# ---------------------------------------------------------------------------
# The in-house loop (NexusPower) must carry identity too — iron rule #9
# ---------------------------------------------------------------------------


def test_nexus_power_spec_preserves_the_identity_headers():
    """NexusPower is a third consumer of the same mcp_servers spec, and it
    connects with its OWN client (``sse_client(url, headers=spec.headers)``).
    If its spec conversion ever dropped headers, caller identity would
    silently stop working on the in-house loop while still working on the two
    CLIs — exactly the "one framework away from breaking" shape iron rule #9
    warns about. Verified live 2026-08-03 against the running module server.
    """
    from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
        McpServerSpec,
    )
    from xyz_agent_context.module._mcp_identity import (
        AGENT_ID_HEADER,
        agent_id_headers,
    )

    agent = "agent_d8795abf5021"
    # The same shape context_runtime injects, converted the way assembly does.
    raw = {"social_network_module": {
        "url": "http://127.0.0.1:7802/sse",
        "headers": agent_id_headers(agent),
    }}
    converted = {
        name: McpServerSpec(
            url=str(spec.get("url", "")),
            headers=dict(spec.get("headers") or {}),
        )
        for name, spec in raw.items()
    }

    spec = converted["social_network_module"]
    assert spec.headers.get(AGENT_ID_HEADER) == agent
    # NexusPower speaks the honest header natively — no bearer workaround
    # needed (unlike codex), but the bearer must survive too so one spec
    # serves every framework.
    assert spec.headers.get("Authorization", "").endswith(agent)


def test_turn_source_survives_the_codex_bearer_channel():
    """PR #229 review: the turn source shipped on the explicit header only,
    which codex drops — so a codex-side asker always wrote NULL and the
    recipient fell back to the "have I spoken here" heuristic, which flips a
    FOLLOW-UP question to Owner Relay and reproduces the P1. It now rides the
    bearer too, which is the one header codex forwards."""
    from xyz_agent_context.module._mcp_identity import (
        BEARER_AGENT_PREFIX,
        BEARER_FIELD_SEP,
        agent_id_headers,
    )

    agent = "agent_d8795abf5021"
    specs = {"social_network_module": {
        "url": "http://localhost:7802/sse",
        "headers": agent_id_headers(agent, turn_source="chat"),
    }}

    token = next(iter(codex_mcp_bearer_env(specs).values()))
    assert token == f"{BEARER_AGENT_PREFIX}{agent}{BEARER_FIELD_SEP}chat"
    # Both facts must be recoverable from that single value.
    ident, _, source = token[len(BEARER_AGENT_PREFIX):].partition(BEARER_FIELD_SEP)
    assert ident == agent
    assert source == "chat"


def test_bearer_without_a_turn_source_is_still_valid():
    """A caller that does not know its own source must not produce a
    malformed bearer."""
    from xyz_agent_context.module._mcp_identity import (
        BEARER_AGENT_PREFIX,
        BEARER_FIELD_SEP,
        agent_id_headers,
    )

    token = next(iter(codex_mcp_bearer_env(
        {"s": {"url": "http://x/sse", "headers": agent_id_headers("agent_a")}}
    ).values()))
    assert token == f"{BEARER_AGENT_PREFIX}agent_a"
    assert BEARER_FIELD_SEP not in token
