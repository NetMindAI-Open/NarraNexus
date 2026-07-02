"""
@file_name: _codex_permission_translator.py
@date: 2026-05-29
@description: Translate NarraNexus's Claude-Code PreToolUse policy
into Codex CLI's ``[permissions]`` config.toml shape.

Claude Code uses per-call ``PreToolUse`` hooks (Python callables that
inspect ``tool_name`` + ``tool_input`` and return deny dicts). Codex
has no per-call hook — its permission model is declarative TOML
configured at process start. We can't move all the dynamic logic
across (e.g. resolving a Read path against ``Path.resolve(strict=False)``
to catch symlink escapes) but we CAN translate the bulk of the
rule set into Codex's glob-pattern filesystem/commands gate.

Output shape (consumed by :func:`_codex_config_toml_builder.build_codex_config_toml`):

    {
        "extends": ":workspace",
        "filesystem": {
            "<workspace>": "write",
            "**": "read",
            "/etc/**": "deny",
            "/root/**": "deny",
        },
        "commands": {
            "brew install *": "deny",
            "npm install -g *": "deny",
            "yarn global add *": "deny",
            "apt-get install *": "deny",
            "apt install *": "deny",
            "sudo *": "deny",
            "pip install *": "deny",
            "lark-cli *": "deny",
        },
        "tools": {
            "WebSearch": "deny",  # only when supports_server_tools=False
        },
    }

Caveats
-------
* **Glob, not regex.** Codex uses shell-style globs; ``*`` matches one
  segment, ``**`` matches multiple. Patterns like
  ``re.compile(r"(?:^|[\\s;&|`$(])brew\\s+(?:cask\\s+)?install\\b")`` from
  ``_tool_policy_guard`` cannot be transcribed exactly; we lose the
  "after pipe / subshell / backtick" anchoring. In practice the agent
  rarely composes commands inside backticks, so the lost coverage is
  marginal — but it IS a coverage loss vs CC's PreToolUse path.
* **No `pip --target=` exemption.** CC's guard allows ``pip install``
  IFF ``--target=`` or ``--user`` is present. Codex glob patterns
  can't express "only when flag X is absent". We deny all
  ``pip install *`` and document the limitation.
* **No symlink-escape detection.** The CC guard does
  ``Path.resolve(strict=False)`` to catch symlinks pointing outside
  the workspace. Codex's filesystem permission is path-prefix based;
  symlink escapes that resolve outside the workspace will silently
  be allowed if their literal path is inside it. This was previously
  mitigated by ``--sandbox workspace-write`` (syscall-level), but
  codex issue #16685 forced us to ``danger-full-access`` so MCP
  tool calls don't get auto-cancelled — so the kernel-level
  symlink-escape protection is now ALSO gone. Live with the gap by:
  (a) trusting the per-agent ``working_path`` boundary at the
  NarraNexus layer, (b) the system-dir and dangerous-command denies
  in this very translator, and (c) the model's system prompt
  scoping behaviour. Revisit when #16685 ships a real fix.
"""

from __future__ import annotations

from pathlib import Path


def translate_tool_policy_to_codex_permissions(
    *,
    workspace: str | Path,
    supports_server_tools: bool = False,
    cloud_mode: bool = True,
) -> dict:
    """Translate the CC tool-policy rules into a dict for Codex toml.

    Args:
        workspace: The per-agent workspace path. Becomes the sole
            writable filesystem root.
        supports_server_tools: When False, ``WebSearch`` is denied.
            Same gate as :func:`build_tool_policy_guard`.
        cloud_mode: When True, the global-install-command denials AND
            the read-scope restriction are active. When False (local
            install — the user's own machine), only the always-on
            denials (Lark shell-out + WebSearch gating) apply.
    """
    workspace_str = str(Path(workspace).resolve(strict=False))

    filesystem: dict[str, str] = {
        workspace_str: "write",
    }
    commands: dict[str, str] = {
        # Always-on: Lark shell-out routing (both cloud + local). The
        # MCP layer hydrates credentials + isolates workspaces; bare
        # lark-cli calls skip both. See _tool_policy_guard._LARK_SHELL_PATTERNS.
        "lark-cli *": "deny",
        "npm install @larksuite/cli *": "deny",
        "npm i @larksuite/cli *": "deny",
        "clawhub install lark-* *": "deny",
        "npx skills add larksuite/cli *": "deny",
    }
    tools: dict[str, str] = {}

    if cloud_mode:
        # Read-scope: anything outside the workspace is read-only.
        # ``**`` is Codex's "anywhere" glob; we set it BELOW the
        # workspace write entry so the more specific path wins.
        filesystem["**"] = "read"
        # Defense-in-depth deny patterns (CC guard catches via Path.resolve
        # but for Codex we have to enumerate).
        filesystem["/etc/**"] = "deny"
        filesystem["/root/**"] = "deny"
        filesystem["/var/**"] = "deny"

        # Global-install command denials (cloud only — local mode is
        # the user's own machine, so global installs are allowed).
        # Mirrors _tool_policy_guard._GLOBAL_INSTALL_PATTERNS but in
        # glob shape instead of regex.
        commands.update({
            "brew install *": "deny",
            "brew cask install *": "deny",
            "npm install -g *": "deny",
            "npm i -g *": "deny",
            "npm -g install *": "deny",
            "npm -g i *": "deny",
            "yarn global add *": "deny",
            "apt install *": "deny",
            "apt-get install *": "deny",
            # sudo blanket-deny: privilege escalation in cloud is never
            # acceptable, regardless of what command follows.
            "sudo *": "deny",
            # pip install bare form. CC guard exempts --target= / --user;
            # Codex globs can't express that, so we deny the bare form
            # and let the agent know via the deny message.
            "pip install *": "deny",
            "pip3 install *": "deny",
        })

    if not supports_server_tools:
        # WebSearch is Anthropic's server-side tool. Codex's built-in
        # web search uses OpenAI's path — fundamentally different. We
        # deny the CC-named tool so a prompt mentioning WebSearch
        # doesn't waste a round trip.
        tools["WebSearch"] = "deny"

    return {
        "extends": ":workspace",
        "filesystem": filesystem,
        "commands": commands,
        "tools": tools,
    }
