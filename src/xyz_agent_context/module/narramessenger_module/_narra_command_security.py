"""
@file_name: _narra_command_security.py
@date: 2026-07-20
@description: Security layer for the generic ``narra_cli`` passthrough MCP tool.

The passthrough hands an arbitrary command string to the local ``narra-cli``
binary. This module keeps that safe with a small, explicit surface (narra-cli
has only ~6 domains):

  - ``ALLOWED_DOMAINS`` whitelist — the first token must be a known narra-cli
    domain.
  - ``BLOCKED_PATTERNS`` — ``configure`` (endpoint is a platform-global concern)
    and ``doctor`` (a probe surface); these are not the agent's to run.
  - ``BLOCKED_FLAGS`` — ``--token`` / ``--token-file`` are INJECTED by
    ``narra_cli_client`` per call; an agent supplying its own would override our
    injection or probe for a readable path, so they are always rejected.
  - ``explore`` is gated to official agents (upstream marks it "Official Agents
    Only").
  - ``shlex.split`` + ``shell=False`` argv is the real injection defense — NOT a
    shell-metachar denylist. A denylist would only break legitimate message
    content ("S&P 500", "$76,000", markdown tables) while adding no safety under
    ``execve`` (the same lesson burned in on the Lark side —
    ``_lark_command_security``).

Mirrors ``lark_module/_lark_command_security.py`` in shape; independent per
binding rule #3 (modules must not import each other).
"""

from __future__ import annotations

import shlex
from typing import Tuple

# Allowed top-level command domains (first token of a narra-cli command).
# Derived from the runtime guide's command surface.
#
# ``explore`` is allowed here and its official-agents-only policy is enforced
# SERVER-SIDE: a non-official agent gets an ``official-agent-required`` JSON
# error from the backend. We deliberately do NOT gate it client-side — we have
# no reliable client-side signal of official status, and a client gate would
# only block everyone (and hide the informative backend error).
ALLOWED_DOMAINS = {
    "room",
    "im",
    "speech",
    "explore",
    "status",
    "help",
}

# Blocked commands (prefix match against the full command string).
#   configure — endpoint is a platform-global concern (run.sh / configure once)
#   doctor    — diagnostic probe surface; not the agent's to run
#   im send   — TRANSITIONAL: sending stays on the Matrix-direct dedicated tools
#               (narra_reply / narra_send / narra_send_media) while the proxy
#               media path (moderation / compound / failure modes) is validated
#               on dev. narra_cli's ``im`` is for messages / attachments only.
#               Remove this block (and the dedicated send tools) once the proxy
#               send/media path is verified. ``im messages`` / ``im attachments``
#               are NOT matched by this prefix and remain allowed.
BLOCKED_PATTERNS = [
    "configure",
    "doctor",
    "im send",
]

# Flags the platform INJECTS per call — the agent must never supply them.
BLOCKED_FLAGS = [
    "--token",
    "--token-file",
]

_ESCAPE_MAP = {
    r"\n": "\n",
    r"\t": "\t",
    r"\r": "\r",
}


# Flags whose VALUE is free-form user-facing text, where an LLM-written ``\n``
# is meant as a real newline. Escape expansion is scoped to these only — never
# to paths (``--out`` / ``--output`` / ``--input``) or search terms
# (``--keyword``), where a literal backslash sequence must survive untouched.
_TEXT_VALUE_FLAGS = {"--text", "--markdown"}


def _expand_escapes(value: str) -> str:
    """Convert literal ``\\n`` / ``\\t`` / ``\\r`` to real chars."""
    for esc, real in _ESCAPE_MAP.items():
        value = value.replace(esc, real)
    return value


def _expand_text_flag_escapes(args: list[str]) -> list[str]:
    """Expand escapes only in the values of text-bearing flags.

    LLMs write ``--text "a\\nb"`` meaning a newline, but ``shlex.split`` keeps the
    backslash literal. We expand the value that follows a text flag (and the
    ``--text=...`` inline form), leaving every other token — paths, regexes,
    ids — byte-for-byte intact.
    """
    out: list[str] = []
    for i, a in enumerate(args):
        prev = args[i - 1] if i > 0 else ""
        if prev in _TEXT_VALUE_FLAGS:
            out.append(_expand_escapes(a))
        elif "=" in a and a.split("=", 1)[0] in _TEXT_VALUE_FLAGS:
            key, val = a.split("=", 1)
            out.append(f"{key}={_expand_escapes(val)}")
        else:
            out.append(a)
    return out


def validate_command(command: str) -> Tuple[bool, str]:
    """Validate a narra-cli command string.

    Args:
        command: the raw command (without the ``narra-cli`` prefix), e.g.
            ``im messages --room-id !r:h --limit 20``.

    Returns:
        ``(True, "")`` if allowed, else ``(False, reason)``.

    Note: ``explore`` passes here; its official-agent restriction is enforced
    server-side (``official-agent-required``), not by this whitelist.
    """
    if not command or not command.strip():
        return False, "Empty command"

    # Tokenize with shlex (the SAME view sanitize_command uses) so every check
    # below agrees. Matching blocked patterns on the raw string instead would
    # let ``im  send`` (extra whitespace) slip past the ``im send`` block while
    # the domain check — which runs on tokens — still saw ``im``. shlex also
    # respects quotes, so whitespace INSIDE a quoted arg is preserved.
    try:
        tokens = shlex.split(command.strip())
    except ValueError as e:
        return False, f"Could not parse command: {e}"
    if not tokens:
        return False, "Empty command"

    lowered = [t.lower() for t in tokens]

    # Injected flags must never come from the agent.
    for flag in BLOCKED_FLAGS:
        if any(t == flag or t.startswith(f"{flag}=") for t in tokens):
            return False, (
                f"Blocked flag: '{flag}' — the platform injects the agent token; "
                "do not pass it yourself"
            )

    # Blocked commands — match leading tokens (whitespace-robust).
    for pattern in BLOCKED_PATTERNS:
        pat = pattern.split()
        if lowered[: len(pat)] == pat:
            return False, (
                f"Blocked command: '{pattern}' — not available via narra_cli"
            )

    # Domain whitelist.
    domain = lowered[0]
    if domain not in ALLOWED_DOMAINS:
        return False, (
            f"Unknown command domain: '{domain}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_DOMAINS))}"
        )

    return True, ""


def sanitize_command(command: str) -> list[str]:
    """Validate, then parse a command string into a safe argv list.

    ``shlex.split`` (proper quote handling) + ``\\n\\t\\r`` escape expansion, then
    handed to ``create_subprocess_exec`` with ``shell=False`` — no character-level
    stripping is needed or wanted (see module docstring).

    Raises:
        ValueError: if the command is blocked, or shlex cannot parse it.
    """
    allowed, reason = validate_command(command)
    if not allowed:
        raise ValueError(reason)

    try:
        args = shlex.split(command.strip())
    except ValueError as e:
        raise ValueError(f"Failed to parse command: {e}")

    return _expand_text_flag_escapes(args)
