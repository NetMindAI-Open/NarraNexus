"""
@file_name: test_command_payload_never_blocks.py
@date: 2026-08-05
@description: Lock the invariant that message PAYLOAD can never trip a
              control-plane rule — whatever those rules happen to be.

Context (prod, 2026-08-04): the daily PM report stopped arriving in the
Nexus algorithm group in full form. Root cause was not the report, the
length, or --markdown: validate_command matched BLOCKED_PATTERNS with
``f" {pattern}" in lower`` against the WHOLE command string, and that
string contains the quoted --markdown/--text body. ``"update"`` was in
the denylist (aimed at lark-cli's self-update subcommand), so every
report containing the word "update"/"updated" was refused with the
misleading ``Blocked command: 'update' — use the dedicated MCP tool``.

The agent probed 8 times (sending "test" messages into a real chat),
concluded the wrong cause, and degraded the report to a terse --text
summary. 42 such blocks are recorded in event_stream since 2026-05-13;
they are 100% of every BLOCKED_PATTERNS hit ever — i.e. the guard had
never once fired on a real command.

The same defect sat in BLOCKED_FLAGS (``flag in stripped``) and in the
heredoc check (``tok.startswith("<<")`` over payload tokens).

These tests are parameterized off the constants themselves, so adding a
new rule automatically inherits the invariant: a rule may constrain what
COMMAND is run, never what MESSAGE is sent.

Sibling precedent: test_command_shell_chars_allowed.py locked the same
lesson for the shell-metacharacter denylist in 2026-04.
"""

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    BLOCKED_FLAGS,
    BLOCKED_PATTERNS,
    validate_command,
)

CHAT = "oc_587c20c16b0a4a574dd320f91fbe07d8"


def _send(body: str, flag: str = "--markdown") -> str:
    return f'im +messages-send --chat-id {CHAT} {flag} "{body}" --as bot'


# ── payload side: rules must be unreachable from the message body ──────────


@pytest.mark.parametrize("pattern", BLOCKED_PATTERNS)
@pytest.mark.parametrize("flag", ["--markdown", "--text"])
def test_blocked_pattern_named_in_message_body_still_sends(pattern, flag):
    """Prose that MENTIONS a blocked command is still just prose."""
    body = f"Reminder for the team: do not run {pattern} yourself — ping ops."
    allowed, reason = validate_command(_send(body, flag))
    assert allowed, f"Body mentioning {pattern!r} was refused: {reason}"


@pytest.mark.parametrize("flag_name", BLOCKED_FLAGS)
def test_blocked_flag_named_in_message_body_still_sends(flag_name):
    """Prose that MENTIONS a blocked flag is still just prose."""
    body = f"Security note: never pass {flag_name} on the command line."
    allowed, reason = validate_command(_send(body))
    assert allowed, f"Body mentioning {flag_name!r} was refused: {reason}"


@pytest.mark.parametrize(
    "body",
    [
        "PM Notes update: Rev 169 to 172.",
        "PM Notes were updated today, no blockers.",
        "Weekly Updates for the team are attached.",
        "<<Summary>> Day 57 went fine.",
        "Cost is $(2 x 3) dollars and the flag is - a dash.",
        "Read report.md then reply -",
        "See https://example.com/docs/update-log for the changelog.",
    ],
)
def test_realistic_report_bodies_send(body):
    """The exact shapes that broke the 2026-08-04 daily report."""
    allowed, reason = validate_command(_send(body))
    assert allowed, f"Refused legitimate report body {body!r}: {reason}"


# ── control side: the rules must still do their job ───────────────────────


@pytest.mark.parametrize("pattern", BLOCKED_PATTERNS)
def test_blocked_pattern_as_actual_command_is_refused(pattern):
    """Removing false positives must not remove the real defense.

    Asserting on the REASON matters: 6 of the 7 patterns would also be
    caught downstream (ALLOWED_DOMAINS for config/profile/update, the auth
    branch for `auth logout`), so a bare `not allowed` would still pass if
    this rule were deleted outright and only `event +subscribe` would
    actually notice.
    """
    allowed, reason = validate_command(pattern)
    assert not allowed, f"{pattern!r} must not be runnable as a command"
    assert "Blocked command" in reason, (
        f"{pattern!r} was refused by a downstream check, not by this rule: {reason}"
    )


@pytest.mark.parametrize("flag_name", BLOCKED_FLAGS)
def test_blocked_flag_as_actual_flag_is_refused(flag_name):
    allowed, reason = validate_command(
        f"im +messages-send --chat-id {CHAT} --text hi {flag_name} sekret"
    )
    assert not allowed
    assert "--app-secret" in reason


@pytest.mark.parametrize(
    "body",
    [
        "--app-secret",             # body IS the flag
        "--app-secret-stdin",
        "--app-secret=xyz please",  # part before the first "=" IS the flag
    ],
)
def test_body_that_parses_as_a_blocked_flag_token_is_refused_by_design(body):
    """The residue of "payload can never trip a rule" — and it is chosen.

    Exactly two shapes reach the flag rule, both straight from
    ``_split_compound_flag``: the body IS a blocked flag, or the body's part
    before its FIRST ``=`` is. Refusing them is the price of never letting a
    secret reach argv, and dev's substring matcher refused them too, so
    nothing widened.

    The allowed side is pinned in the companion test below — together they
    fix the boundary from both directions, so neither a comment nor a name
    can drift into claiming more than the code does.
    """
    allowed, reason = validate_command(_send(body))
    assert not allowed, f"expected the documented trade-off to refuse {body!r}"
    assert "--app-secret" in reason


@pytest.mark.parametrize(
    "body",
    [
        "--app-secret is bad",            # opens with the name but is one token
        "--app-secret please rotate it",
        "--app-secret-stdin is worse",
        # _split_compound_flag DOES split this — into ("--app-secret  ",
        # "  x") — but the name keeps its trailing spaces, so it does not
        # equal "--app-secret".
        "--app-secret  =  x",
        "--foo bar --app-secret=x",       # the flag name is not at the start
    ],
)
def test_body_merely_containing_a_flag_name_still_sends(body):
    """"Opens with a blocked flag name" is NOT the boundary — equality is.

    These all begin with (or contain) a blocked flag name and are all sent:
    each is a single token that neither equals a flag nor has a flag before
    its first ``=``. Pinned because the source comment previously said
    "starts with", which describes a wider set than the code refuses and
    would send a reader hunting for a false positive that does not exist.
    """
    allowed, reason = validate_command(_send(body))
    assert allowed, f"Refused legitimate body {body!r}: {reason}"


@pytest.mark.parametrize(
    "cmd",
    [
        "docs +blocks-create --content --app-secret SEK",
        "im +messages-send --chat-id X --text --app-secret SEK",
        "base +records-create --data --app-secret-stdin",
        "docs +blocks-create --content --app-secret=SEK",
    ],
)
def test_blocked_flag_directly_after_a_payload_flag_is_refused(cmd):
    """The secret hits argv at execve — how lark-cli parses the pair is moot.

    A control-token projection that drops "the token after a payload flag"
    swallows the secret along with it. Caught by review on PR #237: an
    earlier cut of this fix regressed exactly these shapes versus dev.
    """
    allowed, reason = validate_command(cmd)
    assert not allowed, f"{cmd!r} puts a secret in argv and must be refused"
    assert "--app-secret" in reason


@pytest.mark.parametrize(
    "cmd",
    [
        'docs +blocks-create --content -',
        'docs +blocks-create --content "$(cat report.md)"',
    ],
)
def test_payload_integrity_checks_survive(cmd):
    """Whole-value stdin / substitution checks are NOT part of the regression."""
    allowed, _reason = validate_command(cmd)
    assert not allowed, f"{cmd!r} silently loses the payload and must be refused"


@pytest.mark.parametrize(
    "cmd",
    [
        'auth logout "',
        'event +subscribe --key "unterminated',
        "im +messages-send --chat-id X --text 'nope",
    ],
)
def test_unparseable_command_fails_closed(cmd):
    """A stray quote must not empty the control list and skip every rule.

    Reading rules off parsed tokens means a parse failure has to refuse,
    not defer — otherwise `auth logout "` bypasses the denylist entirely.
    """
    allowed, _reason = validate_command(cmd)
    assert not allowed, f"{cmd!r} is unparseable and must be refused, not skipped"


# ── heredoc: shape, not prefix ────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "<<Summary>> Day 57 went fine.",
        "<<摘要>> 今日无阻塞",
        "<<< louder <<< still prose",
        # A real operator token can never contain whitespace (shlex would
        # have split it); only a quoted body can, so this is prose.
        "<< EOF",
    ],
)
def test_prose_opening_with_angle_brackets_sends(body):
    """A body may OPEN with '<<' — that is not a heredoc operator."""
    allowed, reason = validate_command(_send(body))
    assert allowed, f"Refused prose {body!r}: {reason}"


@pytest.mark.parametrize("delim", ["<<EOF", "<<-EOF", "<<'EOF'", '<<"EOF"'])
def test_real_heredoc_operator_still_rejected(delim):
    """A bare `<<DELIM` really would ship the literal delimiter as the body."""
    allowed, _reason = validate_command(f"docs +create --title T --markdown {delim}")
    assert not allowed, f"{delim!r} is a heredoc and must be refused"


# ── breadth: the fix must not narrow what lark-cli can do ─────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "base +records-search --app-token X --table-id Y",
        "sheets +values-append --spreadsheet-token S --range A1",
        "minutes +transcript-get --minute-token M",
        "auth login --scope docs:read --json --no-wait",
        "event +list",
    ],
)
def test_breadth_preserved(cmd):
    """Normal lark-cli usage across domains keeps working."""
    allowed, reason = validate_command(cmd)
    assert allowed, f"Refused legitimate command {cmd!r}: {reason}"
