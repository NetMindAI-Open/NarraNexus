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


# Flags whose value is a payload the agent authored. These are the ones an
# agent tries to fill from a file, and therefore the ones worth naming in
# the `@file` hint.
_PAYLOAD_FLAGS = frozenset({"--content", "--markdown", "--text", "--data"})

_AT_FILE_HINT = (
    "Pass the payload with `--content @relative/path.md` instead "
    "(lark-cli reads the file itself; the path must be RELATIVE to your "
    "workspace — absolute paths are rejected)."
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


def _reject_unexpandable_shell(command: str) -> Tuple[bool, str]:
    """Catch shell constructs that silently produce wrong content.

    See the NOTE at the top of this file: these three cannot work through
    execve, and every one of them fails *quietly* — lark-cli returns
    success with the wrong bytes written.
    """
    try:
        tokens = shlex.split(command.strip())
    except ValueError:
        # Unbalanced quotes — sanitize_command raises on this later with a
        # parse error, which is a clearer message than anything here.
        return True, ""

    for i, tok in enumerate(tokens):
        if tok.startswith("<<"):
            return False, (
                f"Heredoc ('{tok}') is shell syntax. lark_cli executes the CLI "
                f"directly (execve), not through a shell, so it is never "
                f"interpreted. {_AT_FILE_HINT}"
            )
        if _is_whole_command_substitution(tok):
            return False, (
                f"Command substitution ('{tok[:40]}...') is NOT expanded: "
                f"lark_cli executes the CLI directly (execve), not through a "
                f"shell, so this arrives as literal text and would be written "
                f"verbatim — and the call would still report success. "
                f"{_AT_FILE_HINT}"
            )
        if tok == "-" and i > 0 and tokens[i - 1] in _PAYLOAD_FLAGS:
            return False, (
                f"'{tokens[i - 1]} -' means read from stdin, but lark_cli "
                f"never wires stdin to the CLI, so the payload would arrive "
                f"empty. {_AT_FILE_HINT}"
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

    # Check blocked patterns
    lower = stripped.lower()
    for pattern in BLOCKED_PATTERNS:
        if lower.startswith(pattern) or f" {pattern}" in lower:
            return False, f"Blocked command: '{pattern}' — use the dedicated MCP tool instead"

    # Check blocked flags
    for flag in BLOCKED_FLAGS:
        if flag in stripped:
            return False, f"Blocked flag: '{flag}' — secrets must not be passed via CLI args"

    # Shell constructs that cannot survive execve (see the NOTE above).
    ok, reason = _reject_unexpandable_shell(stripped)
    if not ok:
        return False, reason

    # Check domain whitelist
    tokens = stripped.split()
    if not tokens:
        return False, "Empty command after parsing"

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


def sanitize_command(command: str) -> list[str]:
    """Parse command string into safe argument list.

    Uses shlex.split for proper handling of quoted strings, then expands
    common escape sequences (\\n, \\t, \\r) in arg values so rich-text
    flags like --markdown render correctly.
    Raises ValueError if command is blocked.
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

    return [_expand_escapes(a) for a in args]
