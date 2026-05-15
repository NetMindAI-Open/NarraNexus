---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/claude_oauth.py
last_verified: 2026-05-13
stub: false
---

# claude_oauth.py — Claude Code OAuth (host CLI managed)

The Claude Code CLI on the host handles the actual OAuth flow with
Anthropic and persists tokens to ``~/.claude/.credentials.json`` (or
wherever ``CLAUDE_CLI_HOME`` says). NarraNexus never reads / writes
the token directly.

The ``user_providers`` row carries:

* ``api_key=""``  — intentionally blank so ``ClaudeConfig.to_cli_env``
  blanks the env vars and the CLI falls back to its own file.
* ``auth_type='oauth'``
* ``auth_ref='claude-cli:~/.claude/.credentials.json'`` (sentinel)
* ``supports_anthropic_server_tools=True`` — this is the official
  Anthropic backend after all, server-side tools work.

Agent slot only. ``probe()`` checks file existence; token validity is
the CLI's problem.
