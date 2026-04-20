"""
@file_name: _lark_command_security.py
@date: 2026-04-16
@description: Security layer for the generic lark_cli MCP tool.

Validates commands against a whitelist of allowed top-level domains and
a blocklist of dangerous operations. Prevents shell injection and
secret leakage.
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

# Explicitly blocked command patterns (matched against full command string)
BLOCKED_PATTERNS = [
    "config init",          # Must use lark_setup tool
    "config remove",        # Dangerous — removes app config
    "profile remove",       # Must use unbind flow
    "profile add",          # Must use lark_setup tool
    "auth login",           # Must use lark_auth tool (controls OAuth)
    "auth logout",          # Dangerous — revokes tokens
    "event +subscribe",     # Long-running — handled by trigger
    "update",               # lark-cli self-update
]

# Dangerous flags that should never appear in commands
BLOCKED_FLAGS = [
    "--app-secret",
    "--app-secret-stdin",
]

# Shell metacharacters that indicate injection attempts.
# Note: {} and [] are allowed because JSON data uses them (e.g. --data '{"emails":["x@y.com"]}')
# This is safe because subprocess uses shell=False — these are literal args, not shell syntax.
_SHELL_CHARS = re.compile(r"[|;&`$()]")


def validate_command(command: str) -> Tuple[bool, str]:
    """Validate a lark-cli command string.

    Returns:
        (True, "") if allowed
        (False, reason) if blocked
    """
    if not command or not command.strip():
        return False, "Empty command"

    stripped = command.strip()

    # Check for shell metacharacters
    if _SHELL_CHARS.search(stripped):
        return False, "Command contains shell metacharacters"

    # Check blocked patterns
    lower = stripped.lower()
    for pattern in BLOCKED_PATTERNS:
        if lower.startswith(pattern) or f" {pattern}" in lower:
            return False, f"Blocked command: '{pattern}' — use the dedicated MCP tool instead"

    # Check blocked flags
    for flag in BLOCKED_FLAGS:
        if flag in stripped:
            return False, f"Blocked flag: '{flag}' — secrets must not be passed via CLI args"

    # Check domain whitelist
    tokens = stripped.split()
    if not tokens:
        return False, "Empty command after parsing"

    domain = tokens[0].lower()
    if domain not in ALLOWED_DOMAINS:
        return False, f"Unknown command domain: '{domain}'. Allowed: {', '.join(sorted(ALLOWED_DOMAINS))}"

    # Special auth restrictions: only allow read-only subcommands
    if domain == "auth":
        if len(tokens) < 2:
            return False, "auth requires a subcommand (status, check, scopes)"
        sub = tokens[1].lower()
        if sub not in ("status", "check", "scopes", "list"):
            return False, f"auth {sub} is not allowed via lark_cli — use dedicated tools for login/logout"

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

    # Strip shell metacharacters (defense in depth)
    clean = _SHELL_CHARS.sub("", command.strip())

    try:
        args = shlex.split(clean)
    except ValueError as e:
        raise ValueError(f"Failed to parse command: {e}")

    return [_expand_escapes(a) for a in args]
