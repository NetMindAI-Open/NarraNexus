"""Unit tests for the narra-cli passthrough security layer.

The passthrough tool (``narra_cli``) hands an arbitrary command string to the
local ``narra-cli`` binary. These tests pin the whitelist / blocklist that keeps
that safe: only known domains run, the platform-injected ``--token*`` flags can
never be supplied by the agent, ``explore`` is gated to official agents, and
``shlex`` + ``shell=False`` (not a shell-metachar denylist) is the injection
defense — so ordinary message content like "S&P 500" must pass.
"""
import pytest

from xyz_agent_context.module.narramessenger_module._narra_command_security import (
    sanitize_command,
    validate_command,
)


def test_allowed_domain_passes():
    ok, reason = validate_command("im messages --room-id !r:h --limit 20")
    assert ok is True
    assert reason == ""


def test_unknown_domain_blocked():
    ok, reason = validate_command("rm -rf /")
    assert ok is False
    assert "rm" in reason


def test_configure_blocked():
    # Endpoint is a platform-global concern; the agent must not reconfigure it.
    ok, reason = validate_command("configure --endpoint https://evil.test")
    assert ok is False


def test_injected_token_flags_are_rejected():
    # The platform injects --token-file per call; an agent supplying its own
    # --token / --token-file is either overriding our injection or probing for
    # a readable path — always blocked.
    for cmd in (
        "status --token abc123",
        "im messages --room-id !r:h --token-file /etc/passwd",
    ):
        ok, reason = validate_command(cmd)
        assert ok is False, cmd
        assert "token" in reason.lower()


def test_im_send_blocked_but_im_messages_allowed():
    # Transitional: sending stays on the dedicated Matrix-direct tools; narra_cli
    # `im` is for messages/attachments only.
    ok, reason = validate_command("im send --room-id !r:h --text hi")
    assert ok is False
    assert "im send" in reason
    # Sibling subcommands under `im` remain allowed.
    assert validate_command("im messages --room-id !r:h --limit 20")[0] is True
    assert validate_command("im attachments download --event-id e --output ./x")[0] is True


def test_im_send_block_is_whitespace_robust():
    # LLMs emit inconsistent spacing; `im  send` (double space) must NOT slip
    # past the `im send` block (it would otherwise reach the proxy send path).
    ok, reason = validate_command("im   send --room-id !r:h --text hi")
    assert ok is False
    assert "im send" in reason


def test_quoted_internal_whitespace_preserved():
    # The whitespace-robust block must not collapse whitespace INSIDE a quoted
    # arg — shlex respects quotes, so message content survives intact.
    args = sanitize_command('im messages --room-id !r:h --keyword "a  b"')
    assert args[-1] == "a  b"


def test_explore_passes_whitelist_backend_enforces_official():
    # explore is NOT gated client-side — it passes our whitelist, and the
    # backend returns `official-agent-required` for a non-official agent.
    ok, reason = validate_command("explore publish --markdown hello")
    assert ok is True
    assert reason == ""
    assert sanitize_command("explore list --limit 20")[0] == "explore"


def test_empty_command_blocked():
    ok, _ = validate_command("")
    assert ok is False


def test_sanitize_shlex_splits_quoted():
    args = sanitize_command('im messages --room-id !r:h --keyword "hello world"')
    assert args == ["im", "messages", "--room-id", "!r:h", "--keyword", "hello world"]


def test_sanitize_expands_escapes_in_text_flags_only():
    # A text flag's value: \n -> real newline (LLMs write \n meaning newline).
    args = sanitize_command('speech synthesize --text "a\\nb" --out ./r.wav')
    assert args[args.index("--text") + 1] == "a\nb"


def test_sanitize_does_not_touch_non_text_values():
    # Paths / search terms / regexes must keep literal backslash sequences —
    # only text flags are expanded.
    args = sanitize_command('im messages --room-id !r:h --keyword "a\\nb"')
    assert args[-1] == "a\\nb"  # keyword value untouched
    args2 = sanitize_command('speech synthesize --text "x" --out "./a\\nb.wav"')
    assert args2[-1] == "./a\\nb.wav"  # path untouched


def test_message_content_with_shell_metachars_not_rejected():
    # execve + argv (shell=False) makes | ; & $ ( ) literal — a denylist would
    # only break legitimate financial / markdown content. These must pass.
    for text in ("S&P 500", "$76,000", "a | b (c)"):
        cmd = f'im messages --room-id !r:h --keyword "{text}"'
        ok, _ = validate_command(cmd)
        assert ok is True, text
        assert sanitize_command(cmd)[-1] == text


def test_sanitize_blocked_raises():
    with pytest.raises(ValueError):
        sanitize_command("configure --endpoint https://evil.test")


# ── the flag rule's boundary, pinned from BOTH sides ───────────────────────
#
# Ported from the lark guard's 2026-08-05 series (#237→#239→#241). That fix
# took three review rounds because each attempt described the residue in
# prose ("payload can never trip a rule", then "a body starting with a flag
# name is refused") and each description claimed more than the code did.
# The cure was pinning both sides: with REFUSE and ALLOW both asserted,
# neither a comment nor a test name can widen the claim unnoticed.
#
# narra has the SAME residue as lark — payload does reach this rule in
# exactly two shapes — so it gets the same treatment rather than a mirror-md
# sentence claiming it is immune.


@pytest.mark.parametrize(
    "body",
    [
        "--token",           # body IS the flag
        "--token-file",
        "--token=sekret",    # part before the first "=" IS the flag
        "--TOKEN",           # case-folded: argv leaks regardless of spelling
    ],
)
def test_body_that_parses_as_a_blocked_flag_token_is_refused(body):
    ok, reason = validate_command(f'explore --keyword "{body}"')
    assert ok is False, f"expected the documented trade-off to refuse {body!r}"
    assert "--token" in reason


@pytest.mark.parametrize(
    "body",
    [
        "--token is bad",             # opens with the name but is one token
        "--token-file please rotate",
        # This module has no compound-flag split at all: the rule is one
        # line, `t == flag or t.startswith(f"{flag}=")`. This token neither
        # equals "--token" nor starts with "--token=".
        "--token  =  x",
        "never pass --token yourself",
    ],
)
def test_body_merely_containing_a_flag_name_still_sends(body):
    ok, reason = validate_command(f'explore --keyword "{body}"')
    assert ok is True, f"Refused legitimate body {body!r}: {reason}"


@pytest.mark.parametrize("spelling", ["--token", "--TOKEN", "--Token"])
def test_blocked_flag_match_is_case_folded(spelling):
    """`--TOKEN sekret` still puts the secret in argv; the CLI's own parsing
    of that spelling is irrelevant to what ps / crash logs can see."""
    ok, reason = validate_command(f"im messages --room-id !r:h {spelling} sekret")
    assert ok is False, f"{spelling} must be refused"
    assert "--token" in reason
