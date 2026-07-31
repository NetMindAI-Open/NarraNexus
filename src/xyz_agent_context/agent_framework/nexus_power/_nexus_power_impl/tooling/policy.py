"""
@file_name: policy.py
@author: Bin Liang
@date: 2026-07-29
@description: PolicyEngine — an ordered layer list where deny always
wins and a layer's internal exception counts as deny.

Fail-closed is a deliberate home-grown decision (Codex fails open;
OpenClaw's empty allowlist admits) — on a multi-tenant cloud, security
errs toward "off". v1 layers: the disallowed-tools contract (A1) and
workspace confinement. Future layers (platform deny set, repetition
observation, per-agent policy) append; subagent inheritance is passing
the engine INSTANCE to the subagent channel.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ALLOW,
    Decision,
    PolicyContext,
    PolicyVerdict,
    ToolCall,
)

# Builtin argument names that carry filesystem paths (the confinement
# layer's inspection surface).
#: Arguments EVERY builtin interprets as a location on disk.
_PATH_ARG_NAMES = ("path", "file_path", "directory")

#: Per-tool extras, because the same parameter name means different
#: things to different tools: ``glob``'s ``pattern`` IS a path expression
#: (``glob('../../etc/*')`` enumerated the host while every declared path
#: argument looked clean — 2026-07-29 review), while ``grep``'s
#: ``pattern`` is a REGEX and must never be resolved as a path or a
#: search for ``^/etc`` would be denied as an escape. A name-keyed
#: allowlist cannot express that; a tool-keyed one can.
_TOOL_PATH_ARG_NAMES = {
    "glob": ("pattern",),
    "grep": ("glob",),  # grep's file filter is a path expression; its `pattern` is not
}


class PolicyEngine:
    """Runs every layer in order; any deny (or layer crash) denies."""

    def __init__(self, layers: tuple) -> None:
        self._layers = tuple(layers)

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        for layer in self._layers:
            try:
                decision = layer.check(call, ctx)
            except Exception as exc:  # noqa: BLE001 - fail-closed by design
                logger.warning(
                    f"policy layer {type(layer).__name__} crashed on "
                    f"{call.name}: {exc} — denying"
                )
                return Decision(
                    PolicyVerdict.DENY,
                    f"policy layer {type(layer).__name__} failed (fail-closed)",
                )
            if not decision.allowed:
                return decision
        return ALLOW


class DisallowedToolsLayer:
    """Contract A1: the driver-supplied disallowed set MUST take effect."""

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        if call.name in ctx.disallowed_tools:
            return Decision(
                PolicyVerdict.DENY, f"tool '{call.name}' is disallowed for this turn"
            )
        return ALLOW


class WorkspaceConfinementLayer:
    """Path arguments of builtin tools must resolve inside the workspace.

    No silent rewriting: an escape attempt is denied with the resolved
    path named, so the model can correct itself. MCP tools are not
    path-inspected here (their side effects live server-side).
    """

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        if call.name.startswith("mcp__"):
            return ALLOW
        workspace = Path(ctx.tool_ctx.workspace).resolve()
        arg_names = _PATH_ARG_NAMES + _TOOL_PATH_ARG_NAMES.get(call.name, ())
        for arg_name in arg_names:
            raw = call.args.get(arg_name)
            if not isinstance(raw, str) or not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            resolved = candidate.resolve()
            if not resolved.is_relative_to(workspace):
                return Decision(
                    PolicyVerdict.DENY,
                    f"path '{raw}' resolves outside the workspace ({resolved})",
                )
        return ALLOW


class ShellConfinementLayer:
    """Blocks the obvious shell routes out of the workspace.

    Discovered by acceptance case ``safety`` (2026-07-29): the file
    tools correctly denied ``/etc/passwd``, and the model simply ran
    ``bash head -1 /etc/passwd`` instead. Path-argument confinement can
    never cover a shell — the language is Turing-complete, so ANY
    pattern check is defence in depth, not a boundary.

    What this layer honestly is:
      - it denies commands naming a location that leaves the workspace,
        whether written absolutely (``/etc/passwd``, ``~/x``) or
        relatively (``../../etc/passwd``, ``cd ..``);
      - it is NOT a sandbox. The real boundary is the per-user executor
        CONTAINER in the cloud. Desktop/local mode has no OS-level
        sandbox for any framework (claude_code's CLI is equally free) —
        closing that gap belongs with the authorization design.

    The relative case was originally missed — the token loop skipped
    everything that did not start with ``/`` or ``~``, so the shortest
    escape in the book walked through the layer written to stop it
    (2026-07-29 review). Relative tokens are now resolved against the
    workspace ROOT, which is why a bare ``cd ..`` is refused even when
    the shell happens to sit in a subdirectory: this layer cannot see
    the shell's working directory, and guessing wrong in the permissive
    direction is how the hole appeared the first time. The deny message
    names the offending token so the model can rewrite the command
    rather than guess what upset us.
    """

    # Commands whose first token is a harmless shell builtin operating on
    # relative paths only would still trip a naive absolute-path check
    # (e.g. `sed -i 's|/usr/bin|x|'`), so we look for absolute paths in
    # *token* position rather than anywhere in the string.
    _ESCAPE_TOKENS = ("cd /", "cd ~", "pushd /", "chroot")

    def check(self, call: ToolCall, ctx: PolicyContext) -> Decision:
        if call.name not in ("bash", "bash_background", "process"):
            return ALLOW
        command = str(call.args.get("command", ""))
        if not command:
            return ALLOW
        workspace = Path(ctx.tool_ctx.workspace).resolve()
        lowered = command.lower()
        for marker in self._ESCAPE_TOKENS:
            if marker in lowered:
                return Decision(
                    PolicyVerdict.DENY,
                    f"shell command leaves the workspace ({marker.strip()}); "
                    f"work inside {workspace}",
                )
        for token in _shell_tokens(command):
            if not _looks_like_path(token):
                continue
            candidate = Path(token).expanduser()
            if not candidate.is_absolute():
                candidate = workspace / candidate
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            if not resolved.is_relative_to(workspace):
                return Decision(
                    PolicyVerdict.DENY,
                    f"shell command references '{token}' outside the workspace; "
                    f"work inside {workspace}",
                )
        return ALLOW


def _looks_like_path(token: str) -> bool:
    """Is this token worth resolving as a location?

    Absolutes and ``~`` always are. Among relative tokens only those with
    an actual ``..`` SEGMENT are — that is the whole relative-escape
    vocabulary, and testing for the segment rather than the substring
    keeps ordinary arguments (``--x=a..b``, ``file..txt``) out of the
    check. Flags are skipped so ``sed -i`` and friends stay quiet.
    """
    if token.startswith(("/", "~")):
        return True
    if token.startswith("-"):
        return False
    return ".." in Path(token).parts


def _shell_tokens(command: str) -> list[str]:
    """Best-effort tokenization (quoting-aware, never raises)."""
    import shlex

    try:
        return shlex.split(command)
    except ValueError:
        return command.split()
