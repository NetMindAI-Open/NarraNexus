"""
@file_name: test_command_shell_chars_allowed.py
@date: 2026-04-21
@description: Lock the contract that legitimate shell metacharacters inside
              --text / --markdown / --content args pass through unchanged.

Context: an earlier version of _lark_command_security denied any command
containing [|;&`$()] to defend against shell injection. But the executor
uses asyncio.create_subprocess_exec (shell=False, argv list), so those
chars have no special meaning. The denylist was blocking legitimate
message content — financial reports with "S&P 500", "$76,000", markdown
tables with "|", parenthetical prose, etc. — causing agents to fall back
to probe-sending "test" messages until they identified which char was
triggering the block.

These tests prevent re-introducing the denylist.
"""

import pytest

from narranexus_plugins.lark_module._lark_command_security import (
    sanitize_command,
    validate_command,
)


@pytest.mark.parametrize(
    "content",
    [
        "S&P 500 closed at $7,109 (+0.5%)",
        "| col1 | col2 | col3 |",
        "Fed held rates; Powell signaled cuts",
        "Run `make test` to verify",
        "$(whoami) — this is literal, not a subshell",
        "Multi & mixed | special ; $chars (parens) `too`",
    ],
)
def test_validate_allows_shell_metacharacters_in_content(content):
    """validate_command must not reject legitimate content with shell metachars."""
    cmd = f'im +messages-send --chat-id oc_xxx --markdown "{content}"'
    allowed, reason = validate_command(cmd)
    assert allowed, f"Rejected legitimate content {content!r}: {reason}"


def test_sanitize_preserves_metacharacters_in_quoted_arg():
    """Content like 'S&P 500' must arrive at lark-cli byte-for-byte."""
    args = sanitize_command(
        'im +messages-send --chat-id oc_xxx '
        '--markdown "S&P 500 closed at $7,109; Dow held flat"'
    )
    idx = args.index("--markdown")
    assert args[idx + 1] == "S&P 500 closed at $7,109; Dow held flat"


def test_sanitize_preserves_markdown_table_pipes():
    args = sanitize_command(
        'im +messages-send --chat-id oc_xxx '
        '--markdown "| Metric | Value |\n| --- | --- |\n| Close | 7109 |"'
    )
    idx = args.index("--markdown")
    value = args[idx + 1]
    assert "|" in value
    assert value.count("|") == 9


def test_sanitize_preserves_backticks_and_parens():
    args = sanitize_command(
        'im +messages-send --chat-id oc_xxx '
        '--text "Run `make test` (CI runs this on every push)"'
    )
    idx = args.index("--text")
    assert args[idx + 1] == "Run `make test` (CI runs this on every push)"


def test_blocked_patterns_still_enforced():
    """Remove denylist ≠ remove real defenses. auth login must still be refused."""
    allowed, reason = validate_command("auth login --user foo")
    assert not allowed
    assert "auth" in reason.lower()


def test_blocked_flags_still_enforced():
    """--app-secret / --app-secret-stdin must still be refused (credential leak risk)."""
    allowed, reason = validate_command(
        "im +messages-send --app-secret-stdin --chat-id oc_xxx --text hi"
    )
    assert not allowed
    assert "--app-secret" in reason


def test_unknown_domain_still_rejected():
    """ALLOWED_DOMAINS whitelist must still fire for unknown top-level cmds."""
    allowed, reason = validate_command("rm -rf /")
    assert not allowed
    assert "rm" in reason.lower() or "unknown" in reason.lower()
