"""
@file_name: test_api_config_context_vars.py
@author: Bin Liang
@date: 2026-04-16
@description: New ContextVars provider_source / current_user_id default
behaviour and setter/getter roundtrip.
"""
from xyz_agent_context.agent_framework.api_config import (
    set_provider_source,
    get_provider_source,
    set_current_user_id,
    get_current_user_id,
)


def test_provider_source_default_none():
    set_provider_source(None)
    assert get_provider_source() is None


def test_provider_source_roundtrip():
    set_provider_source("system")
    assert get_provider_source() == "system"
    set_provider_source("user")
    assert get_provider_source() == "user"
    set_provider_source(None)
    assert get_provider_source() is None


def test_current_user_id_default_none():
    set_current_user_id(None)
    assert get_current_user_id() is None


def test_current_user_id_roundtrip():
    set_current_user_id("usr_x")
    assert get_current_user_id() == "usr_x"
    set_current_user_id(None)
    assert get_current_user_id() is None


def test_to_cli_env_blanks_claudecode_nested_guard():
    """A backend launched from inside a Claude Code session inherits
    CLAUDECODE; the spawned `claude` CLI then refuses to start (nested-session
    guard, exit 1) — killing the agent loop AND the CLI helper. to_cli_env must
    blank it so the subprocess env is deterministic."""
    from xyz_agent_context.agent_framework.api_config import ClaudeConfig

    env = ClaudeConfig(api_key="sk-x").to_cli_env()
    assert env.get("CLAUDECODE") == ""


def test_to_cli_env_never_points_default_model_redirects_at_aliases():
    """OAuth keeps family aliases verbatim ("opus"), but pointing
    ANTHROPIC_DEFAULT_*_MODEL at an alias is self-referential — the CLI
    rejects the model outright (exit 1), killing every claude_oauth agent
    turn and the CLI helper. Redirects must be blank for alias models and
    concrete for api_key transports."""
    from xyz_agent_context.agent_framework.api_config import ClaudeConfig

    oauth_env = ClaudeConfig(model="opus", auth_type="oauth").to_cli_env()
    assert oauth_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == ""
    assert oauth_env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == ""
    assert oauth_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == ""
    # Subagent pin accepts aliases — keep the family pinned.
    assert oauth_env["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus"

    # api_key transport: alias resolved to a concrete id → redirects set.
    key_env = ClaudeConfig(model="opus", auth_type="api_key", api_key="sk-x").to_cli_env()
    assert key_env["ANTHROPIC_DEFAULT_OPUS_MODEL"].startswith("claude-")
