---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/claude_oauth.py
last_verified: 2026-07-07
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

## 2026-07-07 — helper 槽也由订阅覆盖

新增 `build_cli_helper_config`（framework=claude_code, auth_type=oauth, key 空）。一次登录既服务 agent（build_claude_config）也服务 helper（helper 的结构化调用经 CliHelperSDK 走同一 claude CLI）。

## 2026-07-07 (跟进) — probe 增加 macOS Keychain 回退

Claude Code 在 macOS 上把 OAuth token 存 Keychain(generic password 'Claude Code-credentials'),不写 `~/.claude/.credentials.json`——纯文件探测在所有 Mac 上误报 '✗ credentials file not found'(CLI 实际能跑)。`probe()` 现在文件缺失时用 `security find-generic-password` 查 Keychain(仅判存在、不读密文;非 darwin/出错回落文件结论)。
