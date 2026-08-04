"""
@file_name: test_command_shell_expansion_guard.py
@date: 2026-07-29
@description: Reject the three shell constructs that CANNOT work through the
lark_cli MCP tool, without re-introducing the metacharacter denylist.

`lark_cli` runs `shlex.split` + `create_subprocess_exec` — straight to
execve(), no shell. Agents nevertheless compose shell-flavoured commands, and
the failure is silent: `--content "$(cat report.md)"` writes the 24-character
string `$(cat report.md)` into the document and lark-cli answers
`{"result": "success", "revision_id": N}`. On 2026-07-29 that overwrote a
2746-line Lark doc with one line of command text, and the agent — seeing
success — reported "rewrite complete, 87% smaller". 16 such calls across 5
agents since May.

The guard has to stay narrow. `test_command_shell_chars_allowed.py` documents
why the old denylist was removed: agents writing "S&P 500", "$76,000",
markdown tables with "|", or prose with parentheses got blocked and fell back
to probe-spamming recipients. So this guard fires ONLY on constructs that
carry no legitimate reading:

1. an argument whose ENTIRE value is one command substitution
2. `-` (stdin) on a content flag — the MCP path never wires stdin, so this
   can only ever deliver empty content
3. a bare heredoc token, which shlex leaves behind as a literal `<<EOF` arg
"""

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    sanitize_command,
    validate_command,
)


# ---------------- 1. whole-value command substitution ----------------


@pytest.mark.parametrize(
    ("cmd", "expected_hint"),
    [
        # --content routes to itself: the hint names the real @file mechanism
        (
            'docs +update --doc D --command overwrite --content "$(cat /tmp/pm_notes_clean.md)"',
            "--content @",
        ),
        # im message bodies read nothing: inline advice, no @file route
        (
            'im +messages-send --chat-id oc_x --text "$(cat /tmp/lark_leaderboard.txt)"',
            "inline",
        ),
        (
            'im +messages-send --chat-id oc_x --markdown "$(cat lark_part1.md)"',
            "inline",
        ),
        # --data routes to itself
        (
            'drive metas batch_query --data "$(cat /tmp/b.json | python3 -c \'import json\')"',
            "--data @",
        ),
        # mail --body does not read files; the hint names --body-file, which does
        (
            'mail +send --to a@b.c --subject S --body "$(cat body.html)"',
            "--body-file",
        ),
    ],
)
def test_whole_value_command_substitution_is_rejected(cmd, expected_hint):
    allowed, reason = validate_command(cmd)
    assert not allowed, f"should have been rejected: {cmd}"
    # The rejection must name the alternative that ACTUALLY works for the
    # offending flag (flag→file-route table) — a generic "@file" hint sent
    # mail/im users to flags that don't exist or don't read files.
    assert expected_hint in reason, f"{cmd!r} → {reason!r}"


def test_rejection_names_the_working_alternative():
    # docs v2 carries no --markdown; --content is the real payload flag.
    _, reason = validate_command(
        'docs +update --doc-id D --content "$(cat r.md)"'
    )
    assert "not" in reason.lower() and "shell" in reason.lower()
    assert "--content @" in reason


def test_sanitize_raises_on_command_substitution():
    with pytest.raises(ValueError, match="shell"):
        sanitize_command('im +messages-send --chat-id oc_x --text "$(cat f.txt)"')


# ---------------- the guard must stay narrow -------------------------


@pytest.mark.parametrize(
    "content",
    [
        # verbatim from test_command_shell_chars_allowed.py — must keep passing
        "$(whoami) — this is literal, not a subshell",
        "S&P 500 closed at $7,109 (+0.5%)",
        "| col1 | col2 | col3 |",
        "Run `make test` to verify",
        "Multi & mixed | special ; $chars (parens) `too`",
        # a value that merely ENDS in ')' must not look like a substitution
        "$(whoami) is a shell builtin (as is pwd)",
        # prose about shell scripting, quoting a substitution mid-sentence
        "Use $(date) inside the script, then commit the result",
    ],
)
def test_legitimate_content_still_passes(content):
    allowed, reason = validate_command(
        f'im +messages-send --chat-id oc_x --markdown "{content}"'
    )
    assert allowed, f"false positive on {content!r}: {reason}"


# ---------------- 2. stdin sentinel ----------------------------------


@pytest.mark.parametrize(
    ("cmd", "expected_hint"),
    [
        # fixtures use commands that actually carry the flag; the hint must
        # name the route that works for THAT flag (flag→file-route table)
        ("docs +update --doc D --content -", "--content @"),
        ("drive metas batch_query --data -", "--data @"),
        ("im +messages-send --chat-id oc_x --text -", "inline"),
        ("im +messages-send --chat-id oc_x --markdown -", "inline"),
        ("mail +send --to a@b.c --subject S --body -", "--body-file"),
    ],
)
def test_stdin_dash_is_rejected_on_content_flags(cmd, expected_hint):
    allowed, reason = validate_command(cmd)
    assert not allowed
    assert "stdin" in reason.lower()
    assert expected_hint in reason, f"{cmd!r} → {reason!r}"


def test_plain_hyphen_elsewhere_is_not_stdin():
    """A bare '-' that isn't a content flag's value must not trip the guard."""
    allowed, _ = validate_command('im +messages-send --chat-id oc_x --text "a - b"')
    assert allowed


# ---------------- 3. heredoc ----------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "docs +update --doc D --command append --content - <<'MDEOF'",
        "docs +update --doc D --command append --content - <<EOF",
        "docs +create --title T --markdown <<-EOF",
    ],
)
def test_heredoc_is_rejected(cmd):
    allowed, reason = validate_command(cmd)
    assert not allowed
    assert "heredoc" in reason.lower() or "stdin" in reason.lower()


# ---------------- existing defenses untouched ------------------------


def test_blocked_patterns_still_enforced():
    allowed, reason = validate_command("auth login --user foo")
    assert not allowed
    assert "auth" in reason.lower()


def test_unknown_domain_still_rejected():
    allowed, _ = validate_command("rm -rf /")
    assert not allowed
