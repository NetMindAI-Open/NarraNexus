---
code_file: src/xyz_agent_context/agent_framework/_codex_permission_translator.py
stub: false
last_verified: 2026-06-01
---

## Why it exists

Codex CLI has no per-call ``PreToolUse`` hook (unlike Claude Code's
``ClaudeAgentOptions.hooks``). Its tool-policy model is declarative
TOML configured at process start: ``[permissions.<profile>]`` with
sub-tables for filesystem patterns, command patterns, and tool
allow/deny rules.

NarraNexus's CC wrapper uses ``_tool_policy_guard.py`` (Python
callable) to gate Read/Glob/Grep/WebSearch/Bash calls dynamically.
For Codex we cannot run those checks at call time, so we translate
the same rule set into Codex's static TOML shape. The translator
output is consumed by ``_codex_config_toml_builder.build_codex_config_toml``.

## Design decisions

- **Single function, dict-shaped output.** The builder takes a
  ``dict`` rather than an object so future rule sets (e.g. for a
  third coding-agent framework) can produce the same shape without
  inheritance. Keys: ``extends`` / ``filesystem`` / ``commands`` /
  ``tools``.
- **Cloud-mode toggle.** Two rule tiers:
  - **Always-on** (both cloud and local): Lark shell-out redirects
    (``lark-cli *``, ``npm install @larksuite/cli *``, ...). These
    are MCP-routing rules — direct shell-outs skip credential
    hydration regardless of sandboxing.
  - **Cloud-only**: read-scope restriction (``**`` = ``read``,
    workspace = ``write``, ``/etc`` / ``/root`` / ``/var`` =
    ``deny``), global-install command blocks (``brew``, ``npm -g``,
    ``sudo``, ``apt``, ``pip install``). Local mode is the user's
    own machine; they own the install surface.
- **Server-tool gate.** ``WebSearch`` is denied when
  ``supports_server_tools=False`` — Codex never runs Anthropic's
  server-side ``web_search_20250305`` tool, so the gate is
  effectively always-on for Codex. Kept as a parameter for
  symmetry with ``build_tool_policy_guard``.

## What's lost in translation

CC's ``_tool_policy_guard.py`` uses Python regex with shell-style
anchoring (``re.compile(r"(?:^|[\\s;&|`$(])brew\\s+install\\b")``).
Codex globs cannot replicate that:

- **No subshell anchoring.** Patterns like ``$(brew install ...)``
  inside a larger Bash command may slip through Codex's glob
  matcher.
- **No conditional exemption.** CC allows ``pip install`` IFF
  ``--target=`` / ``--user`` is present. Codex globs can't express
  "match X only when Y is absent" — we deny all bare ``pip install
  *``. We previously relied on the workspace-write sandbox to bound
  damage, but issue #16685 forced us to ``danger-full-access`` to
  keep MCP working — so this layer is now the LAST glob-level guard.
- **No Path.resolve() symlink check.** CC catches a symlink inside
  the workspace pointing outside; Codex's filesystem permission is
  path-prefix based on the literal string. Previously the
  ``--sandbox workspace-write`` flag filled this gap at the syscall
  level, but issue #16685 + ``danger-full-access`` removed that
  belt — symlink escapes that resolve outside the workspace will
  silently be allowed if their literal path is inside it. Treat
  ``working_path`` + this translator's deny list as the only
  remaining barriers until #16685 ships a fix and we can downgrade
  back to ``workspace-write``.

If you tighten the CC ``_tool_policy_guard.py`` ruleset, update
this translator in lockstep — the unit test
``test_codex_permission_translator.py`` covers the standard 5
rules; extend it as new rules land.

## Upstream / downstream

- **Upstream**: ``xyz_codex_cli_sdk.CodexSDK.agent_loop`` calls
  this once per run to derive the permission dict.
- **Downstream**: ``_codex_config_toml_builder.build_codex_config_toml``
  takes the dict and renders the ``[permissions.narranexus]``
  table tree.

## Gotchas

- **Glob patterns are matched against the EXACT command string.**
  ``"sudo *"`` matches ``sudo apt-get install foo`` but does NOT
  match ``somehelper sudo apt-get install foo``. If Codex ships
  with looser matching in a future version, the current ``"sudo
  *"`` would need to become ``"sudo*"`` or similar.
- **``extends = ":workspace"``** inherits Codex's built-in
  workspace permission profile. Without it, you'd need to
  re-declare every default allow/deny. Don't remove unless
  you've audited the full inheritance chain.
