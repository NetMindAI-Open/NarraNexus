"""
@file_name: _lark_command_security.py
@date: 2026-04-22
@description: Security layer for the generic lark_cli MCP tool.

Validates commands against a whitelist of allowed top-level domains and
a blocklist of dangerous operations. Prevents shell injection and
secret leakage.

`auth login` is partially allowed: bare `auth login` and `auth login
--recommend` (initial OAuth bundle) remain blocked — those must go
through `lark_permission_advance` which owns the three-click state
machine. `auth login --scope <X>` is allowed as an incremental
scope top-up, for the case where `--recommend` didn't cover a scope
the Agent encountered at runtime (e.g. `im:message:send_as_user`).
"""

from __future__ import annotations

import re
import shlex
from typing import Tuple

# Allowed top-level command domains (first token after "lark-cli")
ALLOWED_DOMAINS = {
    "im",
    "contact",
    "calendar",
    "docs",
    "task",
    "drive",
    "sheets",
    "base",
    "mail",
    "wiki",
    "event",
    "vc",
    "minutes",
    "whiteboard",
    "approval",
    "schema",
    "api",
    "auth",    # Only specific subcommands allowed (see blocklist)
    "doctor",
}

# Explicitly blocked command patterns (matched against full command string).
# Note: `auth login` is NOT listed here — its handling is subcommand-aware
# (see validate_command below). Only bare / --recommend forms get blocked;
# `auth login --scope X` for incremental top-ups is allowed.
BLOCKED_PATTERNS = [
    "config init",          # Must use lark_setup tool
    "config remove",        # Dangerous — removes app config
    "profile remove",       # Must use unbind flow
    "profile add",          # Must use lark_setup tool
    "auth logout",          # Dangerous — revokes tokens
    "event +subscribe",     # Long-running — handled by trigger
    "update",               # lark-cli self-update
]

# Dangerous flags that should never appear in commands
BLOCKED_FLAGS = [
    "--app-secret",
    "--app-secret-stdin",
]

# NOTE: an earlier version of this file maintained a denylist of shell
# metacharacters ( | ; & ` $ ( ) ) and rejected any command containing
# them. That defense was aimed at shell=True command injection — but the
# executor (lark_cli_client._exec_lark_cli) uses asyncio.create_subprocess_exec
# with an argv list, which goes straight to execve() without a shell. Those
# characters therefore have no special meaning in our path; they're just
# literal bytes in the arg string.
#
# The denylist had a real cost: legitimate message content like "S&P 500",
# "$76,000", markdown tables with "|", or parenthetical prose would fail
# validation. Agents composing a financial report would get blocked, fall
# back to probing (sending "test"/simplified messages to figure out which
# char triggered the block), and end up spamming the recipient with
# incomplete drafts.
#
# The defenses that actually matter are preserved:
#   - ALLOWED_DOMAINS whitelist (only known lark-cli subcommands)
#   - BLOCKED_PATTERNS (auth login/logout, config init — use dedicated tools)
#   - BLOCKED_FLAGS (--app-secret-stdin, --profile — secrets / isolation bypass)
#   - shlex.split + array-arg subprocess (true injection defense)
#
# 2026-07-29 — the SAME no-shell fact has a second consequence, in the
# opposite direction. Because nothing expands them, three shell constructs
# are not "harmless literal bytes" but silent data loss:
#
#   --content "$(cat report.md)"   writes the 16-char string, reports success
#   --content -                    reads a stdin we never wire → empty payload
#   --content - <<'EOF'            heredoc is shell syntax; shlex leaves <<EOF
#
# Prod 2026-07-29: an agent overwrote a 2746-line Lark doc with the literal
# text `$(cat /tmp/pm_notes_clean.md)`, got `{"result":"success"}` back, and
# reported "rewrite complete, 87% smaller". 16 such calls across 5 agents
# since May. lark-cli's own `--content @file` is the working mechanism, but
# it is only documented in `--help`, so the model reaches for shell instead.
#
# _reject_unexpandable_shell below fires ONLY on these three — it is NOT the
# old denylist coming back. It never inspects content for stray `$`, `|`,
# backticks or parens; a substitution is rejected only when it is the
# argument's ENTIRE value, which no prose ever is.


# Payload flag → the flag that ACTUALLY reads a file for the same payload
# (probe-verified per flag, 2026-08-04):
#   None        — nothing does; the value can only be inline. im message
#                 bodies (--text/--markdown: a present @./file still ships
#                 the literal string, success reported) and mail's --body
#                 (nonexistent @file → ok:true, recipient gets the literal).
#   same flag   — the flag itself expands @relative/path (docs/base
#                 --content, --data, --json; missing file errors loudly).
#   other flag  — a sibling flag reads files (mail: --body-file, which
#                 hard-errors on a missing file).
# One table drives BOTH the file-reference guard (fires wherever the
# offending flag itself reads nothing) and the recovery hint (names the
# route that actually works) — the previous flag/domain frozensets gave
# mail users a nonexistent `--content @file` route and left --body
# unguarded (round-3 review).
_FILE_ROUTE: dict[str, "str | None"] = {
    "--text": None,
    "--markdown": None,
    "--body": "--body-file",
    "--content": "--content",
    "--data": "--data",
    # --json is a PAYLOAD only on base/record-style commands; under docs it
    # is a boolean shorthand for --format json — see _payload_flags_for.
    "--json": "--json",
    # docs: '--help' advertises "- reads stdin" but lark_cli never wires
    # stdin, so the payload would arrive empty (same class as --content -).
    "--reference-map": "--reference-map",
}


def _payload_flags_for(tokens: list[str]) -> frozenset:
    """The payload flags that apply to THIS command.

    ``--json`` is domain-dependent (round-4 review, same shape as im's
    --content): a file-reading payload on ``base`` commands, a boolean
    format shorthand everywhere else — treating it as payload under docs
    produced a fake `--json @file` route for a stray ``-``.
    """
    flags = frozenset(_FILE_ROUTE)
    if tokens and tokens[0].lower() != "base":
        return flags - {"--json"}
    return flags

_IM_INLINE_HINT = (
    "This command reads no files and no stdin — put the FULL value "
    "inline as ONE quoted argument, e.g. "
    '--markdown "line one\\n\\nline two".'
)


def _is_im_message_command(tokens: list[str]) -> bool:
    """True for ``im +messages-send`` / ``im +messages-reply``.

    The one place the flag table alone is not enough: ``--content``
    legitimately expands @file under docs/base but under im it is a JSON
    payload with NO @file expansion (probe: hard-rejected as invalid
    JSON). Recovery hints for these commands therefore always give the
    inline advice, whatever the flag. Case-folded — validate_command
    accepts ``IM`` via ``.lower()`` and the hint must not diverge."""
    return (
        len(tokens) >= 2
        and tokens[0].lower() == "im"
        and tokens[1].startswith("+messages-")
    )


_HEREDOC_TOKEN = re.compile(r"""^<<-?(['"]?)[A-Za-z_][A-Za-z0-9_]*\1$""")


def _is_heredoc_token(token: str) -> bool:
    """True only for a bare heredoc operator: ``<<EOF``/``<<-EOF``/``<<'EOF'``.

    The check is on the WHOLE token, deliberately. ``tok.startswith("<<")``
    also matched message bodies that merely open with the characters —
    ``--markdown "<<Summary>> Day 57 went fine."`` was refused with a
    "Heredoc is shell syntax" error that made no sense to the sender. A
    real heredoc is `<<` plus a bare delimiter word and nothing else; prose
    that opens with "<<" carries punctuation, spaces, or non-ASCII after it.
    A shlex token for a real operator never contains whitespace — if it
    does, quotes put it there and it is a message body, not an operator.
    """
    return bool(_HEREDOC_TOKEN.match(token))


def _split_compound_flag(token: str) -> Tuple[str, str]:
    """Normalize ``--flag=value`` to ``(--flag, value)``.

    Returns ``("", "")`` for anything that isn't a compound flag token.
    shlex keeps ``--markdown=@reply.md`` as ONE token, so any pairwise
    (flag, next-token) scan is blind to it — the round-1 review
    reproduced the fake success through exactly this spelling.
    """
    if token.startswith("--") and "=" in token:
        flag, _, value = token.partition("=")
        return flag, value
    return "", ""


def _recovery_hint(flag: str) -> str:
    """Recovery advice for a rejected payload, keyed by the offending flag.

    Only ever recommends a file route that exists AND reads files for
    THIS flag's payload (see ``_FILE_ROUTE``); everything else gets the
    inline advice. Unknown flags get inline plus a pointer at the
    ``--help`` marker, never a concrete flag that may not exist there.
    """
    route = _FILE_ROUTE.get(flag, "")
    if flag and route == flag:
        return (
            f"Pass the payload with `{flag} @relative/path` instead — "
            f"lark-cli reads the file itself (path RELATIVE to your "
            f"workspace; absolute paths are rejected)."
        )
    if route:
        return (
            f"`{flag}` does not read files — either put the FULL value "
            f"inline as ONE quoted argument, or use `{route} "
            f"<relative/path>`, which DOES read the file."
        )
    if flag in _FILE_ROUTE:  # route is None: inline is the only way
        return (
            f"`{flag}` reads no files and no stdin — put the FULL value "
            f"inline as ONE quoted argument, e.g. "
            f'{flag} "line one\\n\\nline two".'
        )
    return (
        "Put the FULL value inline as ONE quoted argument. Only flags "
        "marked '(supports @file)' in this command's --help can read a "
        "file instead."
    )


def _is_whole_command_substitution(value: str) -> bool:
    """True when the value is exactly one `$(...)`, nothing else around it.

    Whole-value is the discriminator that keeps this narrow. Prose that
    merely mentions a substitution — "$(whoami) is a shell builtin (as is
    pwd)" — has its matching paren somewhere in the middle, so it is left
    alone; a payload the agent expected the shell to produce has it at the
    very end.
    """
    v = value.strip()
    if not v.startswith("$("):
        return False
    depth = 0
    for i, ch in enumerate(v):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i == len(v) - 1
    return False


def _reject_unexpandable_shell(tokens: list[str]) -> Tuple[bool, str]:
    """Catch shell constructs that silently produce wrong content.

    See the NOTE at the top of this file: these three cannot work through
    execve, and every one of them fails *quietly* — lark-cli returns
    success with the wrong bytes written.

    Takes already-parsed tokens: validate_command refuses unparseable
    commands before reaching here, so there is no second parse and no
    "deferred on ValueError" path left to disagree with that decision.
    """
    # im message commands override the flag table for hints: no im flag
    # reads files, --content included (see _is_im_message_command).
    im_message = _is_im_message_command(tokens)
    payload_flags = _payload_flags_for(tokens)

    def _hint(flag: str) -> str:
        return _IM_INLINE_HINT if im_message else _recovery_hint(flag)

    for i, tok in enumerate(tokens):
        # --flag=value is one shlex token: check the VALUE half too, or
        # the equals spelling sails past every pairwise check below.
        compound_flag, compound_value = _split_compound_flag(tok)
        prev_flag = tokens[i - 1] if i > 0 and tokens[i - 1].startswith("--") else ""
        offending_flag = compound_flag or prev_flag
        # Whole-token shape, not a "<<" prefix: `--markdown <<-EOF` really is
        # a heredoc that would ship the literal delimiter as the message, but
        # a body may legitimately OPEN with "<<" ("<<Summary>> Day 57 went
        # fine"). Only a bare `<<[-]DELIM` operator token is the former.
        if _is_heredoc_token(tok):
            return False, (
                f"Heredoc ('{tok}') is shell syntax. lark_cli executes the CLI "
                f"directly (execve), not through a shell, so it is never "
                f"interpreted. {_hint(offending_flag)}"
            )
        if _is_whole_command_substitution(tok) or (
            compound_value and _is_whole_command_substitution(compound_value)
        ):
            return False, (
                f"Command substitution ('{tok[:40]}...') is NOT expanded: "
                f"lark_cli executes the CLI directly (execve), not through a "
                f"shell, so this arrives as literal text and would be written "
                f"verbatim — and the call would still report success. "
                f"{_hint(offending_flag)}"
            )
        if (tok == "-" and prev_flag in payload_flags) or (
            compound_value == "-" and compound_flag in payload_flags
        ):
            return False, (
                f"'{offending_flag} -' means read from stdin, but "
                f"lark_cli never wires stdin to the CLI, so the payload would "
                f"arrive empty. {_hint(offending_flag)}"
            )

    return True, ""


def validate_command(command: str) -> Tuple[bool, str]:
    """Validate a lark-cli command string.

    Returns:
        (True, "") if allowed
        (False, reason) if blocked
    """
    if not command or not command.strip():
        return False, "Empty command"

    stripped = command.strip()

    # Both rules below read PARSED TOKENS, never the flat string. Substring
    # matching the flat string is what refused every daily report containing
    # the word "update" on 2026-08-04: the string carries the quoted
    # --markdown/--text body, so a rule about which COMMAND runs was
    # reachable from what the MESSAGE said.
    # NOTE: `parsed` (shlex) is deliberately distinct from the `tokens`
    # (naive .split()) used by the domain/auth section below — that section's
    # tokenization is unchanged by this fix and is not ours to alter here.
    try:
        parsed = shlex.split(stripped)
    except ValueError as exc:
        # Fail CLOSED. Reading rules off parsed tokens means a parse failure
        # must refuse, not defer — otherwise a stray quote (`auth logout "`)
        # yields no tokens and skips every rule below. The old substring
        # matcher happened to catch that shape only by accident.
        return False, f"Could not parse command (check quoting): {exc}"
    lowered = [t.lower() for t in parsed]

    # Blocked patterns — anchored at the LEADING tokens. Position 0 is always
    # the domain and position 1 the subcommand, so a message body (which can
    # only ever sit after a flag) can never satisfy the anchor.
    for pattern in BLOCKED_PATTERNS:
        pat = pattern.lower().split()
        if lowered[: len(pat)] == pat:
            return False, f"Blocked command: '{pattern}' — use the dedicated MCP tool instead"

    # Blocked flags — whole-token equality. Prose that merely NAMES the flag
    # ("Security note: never pass --app-secret on the command line") is one
    # token WITH SPACES and can never equal a flag name.
    #
    # Equality is checked over every token rather than a control-only
    # projection on purpose: a projection that skips the token after a
    # payload flag would drop `--content --app-secret SEK`, and the leak
    # this rule exists to stop happens at execve — the secret is in argv
    # (visible to ps / process auditing / crash logs) regardless of how
    # lark-cli parses the pair.
    #
    # This is NOT zero false positives, and the residue is deliberate. It is
    # exactly two shapes, both following _split_compound_flag's semantics:
    # the body IS a blocked flag ("--app-secret"), or the body's part before
    # its FIRST "=" is ("--app-secret=xyz please"). Anything else sails
    # through, including bodies that merely open with the name —
    # "--app-secret is bad" is one token, has no "=", so `name` is the whole
    # sentence and matches nothing.
    #
    # Refusing those two shapes is the price of never letting a secret reach
    # argv; dev's substring matcher refused them too, so nothing widened.
    # Both sides are pinned, in a pair of tests:
    # test_body_that_parses_as_a_blocked_flag_token_is_refused_by_design
    # (refused) and test_body_merely_containing_a_flag_name_still_sends
    # (allowed).
    for tok in parsed:
        compound_flag, _value = _split_compound_flag(tok)
        name = (compound_flag or tok).lower()
        if name in BLOCKED_FLAGS:
            return False, f"Blocked flag: '{name}' — secrets must not be passed via CLI args"

    # Shell constructs that cannot survive execve (see the NOTE above).
    ok, reason = _reject_unexpandable_shell(parsed)
    if not ok:
        return False, reason

    # Check domain whitelist. `stripped` is non-empty and whitespace-trimmed
    # (see the guard at the top), so str.split() always yields at least one
    # token — the same reasoning that retired the two sibling empty-command
    # branches; this was the third and last of them.
    tokens = stripped.split()
    domain = tokens[0].lower()
    if domain not in ALLOWED_DOMAINS:
        return False, f"Unknown command domain: '{domain}'. Allowed: {', '.join(sorted(ALLOWED_DOMAINS))}"

    # Special auth restrictions
    if domain == "auth":
        if len(tokens) < 2:
            return False, "auth requires a subcommand (status, check, scopes, login)"
        sub = tokens[1].lower()

        # Read-only subcommands: always allowed
        if sub in ("status", "check", "scopes", "list"):
            return True, ""

        # `auth login`: allowed only when targeted at incremental auth —
        # either the MINT side (`--scope <X>`, optionally with `--no-wait`)
        # or the POLL side (`--device-code <D>`, optionally with `--scope`).
        # Bare `auth login` / `auth login --domain` / `auth login
        # --recommend` stay blocked: those forms are the three-click
        # initial flow and must go through `lark_permission_advance`.
        if sub == "login":
            rest = [t.lower() for t in tokens[2:]]
            has_scope = "--scope" in rest
            has_device_code = "--device-code" in rest
            if not has_scope and not has_device_code:
                return False, (
                    "`auth login` without --scope or --device-code must "
                    "go through `lark_permission_advance` (controls the "
                    "three-click state machine). Use `auth login --scope "
                    "<X> --json --no-wait` to mint a device code, then "
                    "`auth login --device-code <D>` to poll."
                )
            if "--recommend" in rest:
                return False, (
                    "`auth login --recommend` is reserved for "
                    "`lark_permission_advance`. Use just `auth login "
                    "--scope <X> --json --no-wait` for incremental grants."
                )
            if "--domain" in rest and not has_scope and not has_device_code:
                # Defensive: `--domain` alone without explicit scope or
                # device-code is effectively the bulk request path that
                # three-click owns. Belt-and-suspenders (already covered
                # by the initial check above, kept for clarity).
                return False, (
                    "`auth login --domain ...` without --scope or "
                    "--device-code must go through "
                    "`lark_permission_advance`."
                )
            return True, ""

        return False, f"auth {sub} is not allowed via lark_cli"

    return True, ""


_ESCAPE_MAP = {
    r"\n": "\n",
    r"\t": "\t",
    r"\r": "\r",
}


def _expand_escapes(value: str) -> str:
    """Convert literal backslash-escape sequences (\\n, \\t, \\r) to real chars.

    LLMs compose shell-ish command strings and naturally write `\\n` to mean
    newline — but shlex.split preserves backslashes literally. Without this
    expansion, `--markdown "hi\\nworld"` reaches lark-cli as `hi\\nworld`
    (7 chars) and Lark renders the literal `\\n` in the bubble instead of a
    line break.
    """
    for esc, real in _ESCAPE_MAP.items():
        value = value.replace(esc, real)
    return value


# Message-body flags whose VALUE is the literal text delivered to the human.
# lark-cli has no @file expansion for these (the @./file convention exists
# only on --json-style flags), so a value that LOOKS like a file reference —
# one token, @-prefixed, with a filename extension — is almost certainly a
# model trying to send a composed file ("--markdown @lark_reply.md", live
# incident 2026-08-04): lark-cli would deliver the literal string and report
# success. Real @-mentions ("@张三 明天…", "@all") either contain spaces or
# lack an extension, so they never match.
# A value that LOOKS like a file reference: @-prefixed single token ending
# in a known document extension. The extension WHITELIST replaces the old
# "any 1-5 alphanumerics" rule, which both missed ".markdown" (8 chars,
# probe: sailed through) and false-positived on bare dotted mentions like
# "@bob.smith" (round-3 review). Known boundary: the compound scan in
# _pairs() treats every token's =-split as a potential (flag, value), so a
# quoted BODY that is literally "--markdown=@x.md" would also trip the
# guard — an acceptable, self-explaining rejection for an adversarial
# corner no real message hits.
_FILE_REFERENCE_RE = re.compile(
    r"^@\S+\.(md|markdown|txt|json|html?|csv|xml|ya?ml|rst"
    r"|log|pdf|docx?|xlsx?|pptx?|py|sh|png|jpe?g)$",
    re.IGNORECASE,
)


def _reject_file_reference_bodies(args: list[str]) -> None:
    """Raise when a file-reference-looking value sits on a flag that
    does not read files.

    Driven by ``_FILE_ROUTE``: fires for every payload flag whose route
    is not itself (im's --text/--markdown read nothing anywhere — docs
    v2 rejects those flags outright — and mail's --body reads nothing
    while --body-file does). Flags that legitimately expand @file
    (--content/--data/--json) are left alone. Covers both the
    ``--flag value`` and the single-token ``--flag=value`` spellings."""
    def _pairs():
        for flag, value in zip(args, args[1:]):
            yield flag, value
        for token in args:
            compound_flag, compound_value = _split_compound_flag(token)
            if compound_flag:
                yield compound_flag, compound_value

    for flag, value in _pairs():
        route = _FILE_ROUTE.get(flag, flag)
        if route != flag and _FILE_REFERENCE_RE.match(value):
            raise ValueError(
                f"{flag} {value}: this looks like a file reference, but "
                f"{flag} does not read files — the value is sent verbatim, "
                f"so the recipient would literally see \"{value}\". "
                f"{_recovery_hint(flag)}"
            )


def sanitize_command(command: str) -> list[str]:
    """Parse command string into safe argument list.

    Uses shlex.split for proper handling of quoted strings, then expands
    common escape sequences (\\n, \\t, \\r) in arg values so rich-text
    flags like --markdown render correctly.
    Raises ValueError if command is blocked, or if a message-body flag
    carries a file-reference-looking value (see
    ``_reject_file_reference_bodies``).
    """
    allowed, reason = validate_command(command)
    if not allowed:
        raise ValueError(reason)

    # shlex.split with array-arg subprocess (shell=False) is the real defense
    # against injection; no character-level stripping needed. See the NOTE
    # at the top of this file for why the previous _SHELL_CHARS.sub()
    # defense-in-depth was removed.
    try:
        args = shlex.split(command.strip())
    except ValueError as e:
        raise ValueError(f"Failed to parse command: {e}")

    _reject_file_reference_bodies(args)

    return [_expand_escapes(a) for a in args]
