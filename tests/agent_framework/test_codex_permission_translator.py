"""
@file_name: test_codex_permission_translator.py
@date: 2026-05-29
@description: Unit tests for translate_tool_policy_to_codex_permissions.

The translator converts CC's PreToolUse policy (Python regex in
_tool_policy_guard.py) into Codex's declarative TOML permission
shape. Tests pin down the dimensions:
  - cloud_mode vs local mode (controls global-install denials)
  - supports_server_tools vs not (controls WebSearch deny)
  - workspace path appears as writable filesystem entry
  - always-on Lark shell-out denial pre-exists in both modes
"""
from __future__ import annotations

from xyz_agent_context.agent_framework._codex_permission_translator import (
    translate_tool_policy_to_codex_permissions,
)


def test_cloud_mode_writes_workspace_and_reads_elsewhere():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/agent-ws-1", supports_server_tools=False, cloud_mode=True
    )
    assert r["filesystem"]["/agent-ws-1"] == "write"
    assert r["filesystem"]["**"] == "read"
    assert r["filesystem"]["/etc/**"] == "deny"
    assert r["filesystem"]["/root/**"] == "deny"


def test_cloud_mode_denies_global_install_commands():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=False, cloud_mode=True
    )
    for pat in ("brew install *", "npm install -g *", "sudo *", "pip install *", "apt install *"):
        assert r["commands"][pat] == "deny", f"missing deny for {pat}"


def test_cloud_mode_denies_websearch_when_no_server_tools():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=False, cloud_mode=True
    )
    assert r["tools"]["WebSearch"] == "deny"


def test_local_mode_allows_global_install():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=False, cloud_mode=False
    )
    assert "sudo *" not in r["commands"]
    assert "brew install *" not in r["commands"]
    assert "pip install *" not in r["commands"]
    # But still denies Lark shell-out (always-on)
    assert r["commands"]["lark-cli *"] == "deny"


def test_local_mode_skips_read_scope_filesystem_block():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=False, cloud_mode=False
    )
    # Workspace is still writable
    assert r["filesystem"]["/x"] == "write"
    # But no global ** read or deny entries
    assert "**" not in r["filesystem"]
    assert "/etc/**" not in r["filesystem"]


def test_server_tools_enabled_skips_websearch_deny():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=True, cloud_mode=True
    )
    assert "WebSearch" not in r["tools"]


def test_lark_shell_out_denied_in_both_modes():
    for cloud in (True, False):
        r = translate_tool_policy_to_codex_permissions(
            workspace="/x", supports_server_tools=False, cloud_mode=cloud
        )
        assert r["commands"]["lark-cli *"] == "deny"
        assert r["commands"]["npm install @larksuite/cli *"] == "deny"


def test_returns_extends_workspace_default():
    r = translate_tool_policy_to_codex_permissions(
        workspace="/x", supports_server_tools=False, cloud_mode=True
    )
    assert r["extends"] == ":workspace"
