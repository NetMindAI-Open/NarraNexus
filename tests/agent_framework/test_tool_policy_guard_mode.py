"""
@file_name: test_tool_policy_guard_mode.py
@author: Bin Liang
@date: 2026-04-20
@description: ``build_tool_policy_guard`` is deployment-mode aware (Bug 5 step 3/4).

Cloud mode keeps the existing workspace sandbox AND blocks global CLI
installation (``brew``, ``npm install -g``, ``pip install`` without
``--target``/``--user``, ``apt-get``, ``sudo``). Local mode skips both
— the user owns the machine.

The lark-cli shell-out redirection is not about isolation, it's about
MCP routing, so it stays active in both modes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.agent_framework._tool_policy_guard import (
    build_tool_policy_guard,
)


async def _call(guard, tool_name, **tool_input):
    return await guard(
        {"tool_name": tool_name, "tool_input": tool_input},
        None,
        None,
    )


def _is_denied(result) -> bool:
    return (
        result
        and result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


def _reason(result) -> str:
    return result["hookSpecificOutput"]["permissionDecisionReason"]


# -------- Workspace containment: cloud strict, local off ----------------


@pytest.mark.asyncio
async def test_cloud_blocks_read_outside_workspace(tmp_path):
    guard = build_tool_policy_guard(workspace=tmp_path, mode="cloud")
    result = await _call(guard, "Read", file_path="/etc/passwd")
    assert _is_denied(result)
    assert "workspace" in _reason(result).lower()


@pytest.mark.asyncio
async def test_local_allows_read_outside_workspace(tmp_path):
    guard = build_tool_policy_guard(workspace=tmp_path, mode="local")
    result = await _call(guard, "Read", file_path="/etc/passwd")
    # Local mode does NOT enforce workspace containment → pass-through
    assert not _is_denied(result)


@pytest.mark.asyncio
async def test_read_inside_workspace_allowed_in_both_modes(tmp_path):
    (tmp_path / "skills").mkdir()
    inside = tmp_path / "skills" / "notes.md"
    inside.write_text("hi")
    for mode in ("cloud", "local"):
        guard = build_tool_policy_guard(workspace=tmp_path, mode=mode)
        result = await _call(guard, "Read", file_path=str(inside))
        assert not _is_denied(result), f"mode={mode} should allow workspace read"


# -------- Global-install blocking: cloud on, local off ------------------


_GLOBAL_INSTALL_COMMANDS = [
    "brew install steipete/tap/gogcli",
    "brew cask install google-chrome",
    "npm install -g @something/cli",
    "npm i -g yarn",
    "yarn global add foo",
    "pip install requests",                  # no --target/--user
    "sudo apt-get install -y cowsay",
    "sudo bash install.sh",
    "apt-get install -y vim",
]


@pytest.mark.asyncio
async def test_cloud_blocks_global_install_bash_commands(tmp_path):
    guard = build_tool_policy_guard(workspace=tmp_path, mode="cloud")
    for cmd in _GLOBAL_INSTALL_COMMANDS:
        result = await _call(guard, "Bash", command=cmd)
        assert _is_denied(result), f"cloud should deny: {cmd!r}"
        assert "global" in _reason(result).lower() or "sandbox" in _reason(result).lower()


@pytest.mark.asyncio
async def test_local_allows_global_install_bash_commands(tmp_path):
    guard = build_tool_policy_guard(workspace=tmp_path, mode="local")
    for cmd in _GLOBAL_INSTALL_COMMANDS:
        result = await _call(guard, "Bash", command=cmd)
        assert not _is_denied(result), (
            f"local should ALLOW: {cmd!r} (user's own machine)"
        )


@pytest.mark.asyncio
async def test_cloud_allows_scoped_pip_install(tmp_path):
    """``pip install --target=...`` or ``--user`` writes to a bounded
    location, not system site-packages. Should NOT be blocked."""
    guard = build_tool_policy_guard(workspace=tmp_path, mode="cloud")
    safe_cases = [
        "pip install --target=./libs requests",
        "pip install --user cowsay",
    ]
    for cmd in safe_cases:
        result = await _call(guard, "Bash", command=cmd)
        assert not _is_denied(result), (
            f"scoped pip install should be allowed: {cmd!r}"
        )


# -------- Lark shell-out rule: always on (both modes) -------------------


@pytest.mark.asyncio
async def test_lark_shell_out_blocked_in_both_modes(tmp_path):
    """``lark-cli ...`` via Bash bypasses MCP routing, regardless of
    deployment mode. Block in both."""
    for mode in ("cloud", "local"):
        guard = build_tool_policy_guard(workspace=tmp_path, mode=mode)
        result = await _call(guard, "Bash", command="lark-cli contact +get-user")
        assert _is_denied(result), (
            f"mode={mode}: lark-cli shell-out should always be blocked"
        )
