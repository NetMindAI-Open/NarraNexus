---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/_lark_workspace.py
stub: false
last_verified: 2026-04-16
---

## Why it exists

Manages per-agent workspace directories for HOME-based lark-cli isolation.
Each agent gets `~/.narranexus/lark_workspaces/{agent_id}/` where lark-cli
reads/writes its `~/.lark-cli/` config and cache.

## Design decisions

- **HOME override strategy** — only used for `config init --new` (interactive
  setup flow that doesn't support `--profile`). All other commands use
  `--profile agent_{agent_id}` via `_run()`.
- **`get_home_env` inherits full parent env** — needed for macOS Keychain
  access, Node.js paths, etc. Only HOME is overridden.
- **Path traversal protection** — `get_workspace_path` replaces `/` and `..`
  in agent_id with `_`.
- **Restrictive permissions (0o700)** — workspace directories are owner-only.
  Falls back gracefully on Windows.

## Upstream / downstream

- **Upstream**: `lark_cli_client.py` (`_run_with_home`), `_lark_mcp_tools.py`
  (`lark_setup`), `_lark_service.py` (formerly `do_bind`, now only used for
  Quick Setup).
- **Downstream**: filesystem only.

## Gotchas

- On macOS, changing HOME causes Keychain access prompts for apps that use
  the system Keychain (including `lark-cli` when storing tokens). This is
  why `--profile` is the primary isolation strategy and HOME is only used
  for `config init --new`.
- `cleanup_workspace` uses `shutil.rmtree(ignore_errors=True)` — it won't
  fail on permission errors but may leave files behind on Windows.
