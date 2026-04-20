"""
Unit tests for the PreToolUse tool-policy hook.

Two policies in one guard:

1. Workspace-scoped reads — ``Read`` / ``Glob`` / ``Grep`` cannot escape
   the per-agent workspace directory.
2. Server-tool gating — ``WebSearch`` is denied when the current provider
   doesn't advertise support for Anthropic's server-side tools.

Run via:
    uv run pytest tests/agent_framework/test_tool_policy_guard.py -v
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from xyz_agent_context.agent_framework._tool_policy_guard import (
    build_tool_policy_guard,
    build_workspace_read_guard,  # legacy alias
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hook_input(tool_name: str, **tool_input: object) -> dict[str, object]:
    """Minimal PreToolUse hook-input payload matching the SDK shape."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "test-tool-use",
        "session_id": "test-session",
        "transcript_path": "",
        "cwd": "",
        "permission_mode": "bypassPermissions",
    }


async def _invoke(hook, payload):
    return await hook(payload, "tool-use-id", None)


def _is_deny(result: dict) -> bool:
    out = result.get("hookSpecificOutput", {})
    return out.get("permissionDecision") == "deny"


def _deny_reason(result: dict) -> str:
    return result["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "inside.txt").write_text("ok")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.py").write_text("print('hi')")
    return tmp_path


@pytest.fixture()
def guard_no_server_tools(workspace: Path):
    """The common case: aggregator provider like NetMind.

    Pinned to cloud mode because the workspace-containment assertions in
    this file are the cloud sandbox contract. Mode-branching between
    cloud and local is covered separately in
    ``test_tool_policy_guard_mode.py``.
    """
    return build_tool_policy_guard(
        workspace, supports_server_tools=False, mode="cloud"
    )


@pytest.fixture()
def guard_with_server_tools(workspace: Path):
    """Official Anthropic / transparent proxy."""
    return build_tool_policy_guard(
        workspace, supports_server_tools=True, mode="cloud"
    )


# ---------------------------------------------------------------------------
# Workspace read policy — allow cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_absolute_path_inside_workspace_allowed(
    guard_no_server_tools, workspace
):
    payload = _make_hook_input("Read", file_path=str(workspace / "inside.txt"))
    assert await _invoke(guard_no_server_tools, payload) == {}


@pytest.mark.asyncio
async def test_read_relative_path_resolves_inside_workspace(guard_no_server_tools):
    payload = _make_hook_input("Read", file_path="sub/nested.py")
    assert await _invoke(guard_no_server_tools, payload) == {}


@pytest.mark.asyncio
async def test_grep_without_path_uses_cwd_and_is_allowed(guard_no_server_tools):
    payload = _make_hook_input("Grep", pattern="foo")
    assert await _invoke(guard_no_server_tools, payload) == {}


@pytest.mark.asyncio
async def test_glob_pattern_inside_workspace_allowed(guard_no_server_tools):
    payload = _make_hook_input("Glob", pattern="**/*.py", path="sub")
    assert await _invoke(guard_no_server_tools, payload) == {}


@pytest.mark.asyncio
async def test_unrelated_tool_not_blocked(guard_no_server_tools):
    # Write/Edit/Bash/TodoWrite must pass through untouched.
    for tool in ("Write", "Edit", "Bash", "TodoWrite", "NotebookEdit"):
        payload = _make_hook_input(tool, file_path="/etc/passwd", content="x")
        assert await _invoke(guard_no_server_tools, payload) == {}, tool


# ---------------------------------------------------------------------------
# Workspace read policy — deny cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_absolute_path_outside_workspace_denied(guard_no_server_tools):
    payload = _make_hook_input("Read", file_path="/etc/passwd")
    assert _is_deny(await _invoke(guard_no_server_tools, payload))


@pytest.mark.asyncio
async def test_read_relative_dotdot_escape_denied(guard_no_server_tools):
    payload = _make_hook_input("Read", file_path="../outside.txt")
    assert _is_deny(await _invoke(guard_no_server_tools, payload))


@pytest.mark.asyncio
async def test_grep_with_explicit_outside_path_denied(guard_no_server_tools):
    payload = _make_hook_input("Grep", pattern="SECRET", path="/var/log")
    assert _is_deny(await _invoke(guard_no_server_tools, payload))


@pytest.mark.asyncio
async def test_symlink_escape_is_resolved_and_denied(
    guard_no_server_tools, workspace, tmp_path_factory
):
    outside_dir = tmp_path_factory.mktemp("outside")
    secret = outside_dir / "secret.txt"
    secret.write_text("top-secret")
    link = workspace / "escape_link"
    os.symlink(secret, link)

    payload = _make_hook_input("Read", file_path=str(link))
    assert _is_deny(await _invoke(guard_no_server_tools, payload))


@pytest.mark.asyncio
async def test_deny_reason_mentions_workspace(guard_no_server_tools):
    payload = _make_hook_input("Read", file_path="/etc/shadow")
    out = await _invoke(guard_no_server_tools, payload)
    assert "workspace" in _deny_reason(out).lower()


# ---------------------------------------------------------------------------
# Server-tool gate — WebSearch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websearch_denied_when_server_tools_unsupported(guard_no_server_tools):
    payload = _make_hook_input("WebSearch", query="today's weather")
    out = await _invoke(guard_no_server_tools, payload)
    assert _is_deny(out)
    reason = _deny_reason(out).lower()
    # Reason must point the LLM at the right alternative.
    assert "server-side" in reason or "server side" in reason
    assert "webfetch" in reason


@pytest.mark.asyncio
async def test_websearch_allowed_when_server_tools_supported(
    guard_with_server_tools,
):
    payload = _make_hook_input("WebSearch", query="today's weather")
    assert await _invoke(guard_with_server_tools, payload) == {}


@pytest.mark.asyncio
async def test_webfetch_always_allowed(guard_no_server_tools, guard_with_server_tools):
    # WebFetch runs locally; it's NOT a server-side tool. Both providers
    # should allow it unconditionally.
    payload = _make_hook_input("WebFetch", url="https://example.com", prompt="...")
    assert await _invoke(guard_no_server_tools, payload) == {}
    assert await _invoke(guard_with_server_tools, payload) == {}


# ---------------------------------------------------------------------------
# Guard factory semantics
# ---------------------------------------------------------------------------


def test_guard_is_builder_factory(workspace):
    a = build_tool_policy_guard(workspace, supports_server_tools=False, mode="cloud")
    b = build_tool_policy_guard(workspace, supports_server_tools=True, mode="cloud")
    assert a is not b
    assert asyncio.iscoroutinefunction(a)
    assert asyncio.iscoroutinefunction(b)


def test_legacy_alias_preserved(workspace):
    # Existing code paths importing the old name must still work.
    legacy = build_workspace_read_guard(workspace)
    assert asyncio.iscoroutinefunction(legacy)


# ---------------------------------------------------------------------------
# Concurrency: per-task ClaudeConfig must not leak through to_cli_env.
# This is the concern that drove the whole redesign: two users, same
# process, different providers, concurrent requests — env dicts must
# stay independent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_to_cli_env_is_task_local_under_concurrency():
    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig,
        _claude_ctx,
    )

    cfg_a = ClaudeConfig(
        api_key="key-A",
        base_url="https://provider-a.example/anthropic",
        model="provider-a/model",
        auth_type="bearer_token",
        supports_anthropic_server_tools=False,
    )
    cfg_b = ClaudeConfig(
        api_key="key-B",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4-6",
        auth_type="api_key",
        supports_anthropic_server_tools=True,
    )

    async def _snapshot_env(cfg: ClaudeConfig) -> dict[str, str]:
        _claude_ctx.set(cfg)
        # Let sibling task run in between — this exercises the race.
        await asyncio.sleep(0)
        return cfg.to_cli_env()

    env_a, env_b = await asyncio.gather(
        _snapshot_env(cfg_a),
        _snapshot_env(cfg_b),
    )

    # Auth: each task's env uses its own key in the right slot, and the
    # other slot is explicitly blanked so os.environ cannot leak it.
    assert env_a["ANTHROPIC_AUTH_TOKEN"] == "key-A"
    assert env_a["ANTHROPIC_API_KEY"] == ""
    assert env_b["ANTHROPIC_API_KEY"] == "key-B"
    assert env_b["ANTHROPIC_AUTH_TOKEN"] == ""

    # Base URL and all four model overrides route to the task's provider.
    assert env_a["ANTHROPIC_BASE_URL"] == cfg_a.base_url
    assert env_a["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == cfg_a.model
    assert env_a["CLAUDE_CODE_SUBAGENT_MODEL"] == cfg_a.model

    assert env_b["ANTHROPIC_BASE_URL"] == cfg_b.base_url
    assert env_b["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == cfg_b.model
    assert env_b["CLAUDE_CODE_SUBAGENT_MODEL"] == cfg_b.model

    # Ensure no cross-contamination.
    assert env_a["ANTHROPIC_BASE_URL"] != env_b["ANTHROPIC_BASE_URL"]


@pytest.mark.asyncio
async def test_to_cli_env_blanks_keys_even_when_model_empty():
    """Without an explicit model we must still blank the override vars so
    a stale inherited value from os.environ cannot steer the CLI."""
    from xyz_agent_context.agent_framework.api_config import ClaudeConfig

    cfg = ClaudeConfig(api_key="k", base_url="https://x", model="", auth_type="api_key")
    env = cfg.to_cli_env()

    for key in (
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "ANTHROPIC_AUTH_TOKEN",  # api_key auth path — bearer slot must blank
    ):
        assert env[key] == "", f"{key} should be blanked when empty, got {env[key]!r}"


@pytest.mark.asyncio
async def test_to_cli_env_full_keyset():
    """The env must include every key we rely on — no silent gaps."""
    from xyz_agent_context.agent_framework.api_config import ClaudeConfig

    cfg = ClaudeConfig(api_key="k", base_url="https://x", model="m", auth_type="bearer_token")
    env = cfg.to_cli_env()

    expected = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    }
    missing = expected - env.keys()
    assert not missing, f"missing env keys: {missing}"
