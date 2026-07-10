"""
@file_name: test_model_alias_normalization.py
@author: Bin Liang
@date: 2026-07-03
@description: CLI family aliases must never reach a raw Anthropic-compatible
              API (upstream #57).

"opus"/"sonnet"/"haiku" are Claude Code CLI conveniences — only the OAuth/CLI
path resolves them. The raw Messages API rejects them with 400, which the
runtime surfaces as no_reply. Model strings are free text end to end (the
backend does not validate them against the catalog), so an alias can reach an
api_key/bearer provider through manual model edits or future list reuse.
``resolve_cli_alias`` is the single normalization point: alias → the family's
current full id for every non-OAuth transport, everything else passes through.
"""

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.model_catalog import (
    get_all_known_models,
    resolve_cli_alias,
)


def test_alias_resolves_to_full_id_for_api_key_transport():
    assert resolve_cli_alias("opus", auth_type="api_key").startswith("claude-opus-")
    assert resolve_cli_alias("sonnet", auth_type="api_key").startswith("claude-sonnet-")
    assert resolve_cli_alias("haiku", auth_type="api_key").startswith("claude-haiku-")


def test_alias_resolves_for_bearer_token_transport():
    # Bearer proxies forward to Anthropic-compatible endpoints — aliases are
    # just as invalid there as on the official API.
    assert resolve_cli_alias("opus", auth_type="bearer_token").startswith("claude-opus-")


def test_alias_kept_verbatim_on_oauth_transport():
    # The CLI's own session resolves family aliases; keep them (by design —
    # "latest of family" must not go stale in our code).
    for alias in ("opus", "sonnet", "haiku"):
        assert resolve_cli_alias(alias, auth_type="oauth") == alias


def test_full_ids_and_unknown_models_pass_through():
    assert resolve_cli_alias("claude-opus-4-8", auth_type="api_key") == "claude-opus-4-8"
    assert resolve_cli_alias("deepseek-chat", auth_type="api_key") == "deepseek-chat"
    assert resolve_cli_alias("", auth_type="api_key") == ""


def test_alias_targets_are_registered_catalog_models():
    """The mapping must point at real catalog entries — a typo'd or removed
    target would silently reintroduce the 400."""
    known = get_all_known_models()
    for alias in ("opus", "sonnet", "haiku"):
        resolved = resolve_cli_alias(alias, auth_type="api_key")
        assert resolved != alias
        assert resolved in known, resolved


def test_to_cli_env_normalizes_alias_for_api_key():
    env = ClaudeConfig(model="opus", auth_type="api_key").to_cli_env()
    for key in (
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert env[key].startswith("claude-opus-"), (key, env[key])


def test_to_cli_env_keeps_alias_for_oauth():
    """OAuth keeps the alias for the SUBAGENT pin, but the
    ANTHROPIC_DEFAULT_*_MODEL redirects must stay BLANK: pointing an alias
    at itself makes the CLI reject the model (exit 1) — proven live
    2026-07-07 ("There's an issue with the selected model (opus)")."""
    env = ClaudeConfig(model="opus", auth_type="oauth").to_cli_env()
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "opus"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == ""
