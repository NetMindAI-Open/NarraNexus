"""
@file_name: test_codex_config_toml_builder.py
@date: 2026-05-29
@description: Tests for build_codex_config_toml — string output shape.

We don't fully parse the TOML (would require a dep); instead we
look for specific table headers and key=value lines. Sufficient
to lock in the rendering contract.
"""
from __future__ import annotations

from pathlib import Path

from xyz_agent_context.agent_framework.api_config import CodexConfig
from xyz_agent_context.agent_framework._codex_config_toml_builder import (
    build_codex_config_toml,
)
from xyz_agent_context.agent_framework._codex_permission_translator import (
    translate_tool_policy_to_codex_permissions,
)


def _build_minimal() -> str:
    return build_codex_config_toml(
        instructions_path=Path("/tmp/agent/instructions.md"),
        mcp_server_urls={},
        config=CodexConfig(),
        permissions={},
    )


def test_minimal_emits_instructions_path_and_sandbox_mode():
    t = _build_minimal()
    assert 'model_instructions_file = "/tmp/agent/instructions.md"' in t
    # Default sandbox is ``danger-full-access`` — required for MCP to
    # work in codex exec mode (issue #16685). Asserting the exact
    # string also guards against silent re-downgrade to workspace-write.
    assert 'sandbox_mode = "danger-full-access"' in t
    # Reasoning summary must be ``detailed`` — codex CLI defaults to
    # ``none`` which leaves the UI's Thinking panel empty. See the
    # 2026-06-04 empirical comparison in the builder docstring.
    assert 'model_reasoning_summary = "detailed"' in t


def test_minimal_omits_mcp_servers_and_model_providers():
    t = _build_minimal()
    assert "[mcp_servers." not in t
    assert "[model_providers." not in t
    assert "model_provider =" not in t


def test_minimal_omits_writable_roots_and_permissions():
    t = _build_minimal()
    assert "sandbox_workspace_write" not in t
    assert "[permissions." not in t


def test_with_mcp_servers_emits_one_table_per_server():
    t = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={
            "lark_module": "http://localhost:7820/sse",
            "slack_module": "http://localhost:7831/sse",
        },
        config=CodexConfig(),
        permissions={},
    )
    assert "[mcp_servers.lark_module]" in t
    assert "[mcp_servers.slack_module]" in t
    assert 'url = "http://localhost:7820/sse"' in t
    assert 'url = "http://localhost:7831/sse"' in t


def test_mcp_servers_emitted_in_sorted_order_deterministic():
    """Ensure two invocations produce byte-identical output."""
    args = dict(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={"z_module": "http://z/sse", "a_module": "http://a/sse"},
        config=CodexConfig(),
        permissions={},
    )
    t1 = build_codex_config_toml(**args)
    t2 = build_codex_config_toml(**args)
    assert t1 == t2
    # And a_module appears before z_module
    assert t1.index("[mcp_servers.a_module]") < t1.index("[mcp_servers.z_module]")


def test_custom_base_url_adds_model_providers_block():
    t = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(base_url="https://api.netmind.ai/v1", model="gpt-5.4-codex"),
        permissions={},
    )
    assert 'model_provider = "narranexus"' in t
    assert "[model_providers.narranexus]" in t
    assert 'base_url = "https://api.netmind.ai/v1"' in t
    assert 'env_key = "CODEX_API_KEY"' in t
    assert 'wire_api = "responses"' in t


def test_model_in_top_level_only_when_set():
    t_no_model = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(),
        permissions={},
    )
    assert 'model = "' not in t_no_model

    t_model = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(model="gpt-5.4-codex"),
        permissions={},
    )
    assert 'model = "gpt-5.4-codex"' in t_model


def test_writable_roots_emits_sandbox_block():
    t = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(),
        permissions={},
        writable_roots=[Path("/tmp/agent_xyz"), Path("/scratch")],
    )
    assert "[sandbox_workspace_write]" in t
    assert "/tmp/agent_xyz" in t
    assert "/scratch" in t


def test_permissions_block_with_filesystem_commands_tools():
    perms = translate_tool_policy_to_codex_permissions(
        workspace="/ws-1", supports_server_tools=False, cloud_mode=True
    )
    t = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(),
        permissions=perms,
    )
    assert "[permissions.narranexus]" in t
    assert "[permissions.narranexus.filesystem]" in t
    assert "[permissions.narranexus.commands]" in t
    assert "[permissions.narranexus.tools]" in t
    assert 'extends = ":workspace"' in t
    assert 'default_permissions = "narranexus"' in t


def test_permissions_glob_keys_are_quoted():
    perms = translate_tool_policy_to_codex_permissions(
        workspace="/ws-1", supports_server_tools=False, cloud_mode=True
    )
    t = build_codex_config_toml(
        instructions_path=Path("/x/i.md"),
        mcp_server_urls={},
        config=CodexConfig(),
        permissions=perms,
    )
    # Glob path keys must be quoted to be valid TOML
    assert '"**" = "read"' in t
    assert '"/etc/**" = "deny"' in t
    assert '"sudo *" = "deny"' in t


def test_output_ends_with_newline():
    t = _build_minimal()
    assert t.endswith("\n")
