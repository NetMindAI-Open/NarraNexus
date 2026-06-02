---
code_file: src/xyz_agent_context/module/lark_module/_lark_service.py
stub: false
last_verified: 2026-05-22
---

## Why it exists

Shared Lark business logic that both the HTTP routes (`backend/routes/lark.py`)
and MCP tools (`_lark_mcp_tools.py`) need.  Lives in the core package to
avoid circular imports — the API layer imports from here, never the other
way around.

## Design decisions

- **`do_bind()`** — single implementation of the bind flow: validate,
  register CLI profile via `config_init` (--profile based), save credential,
  fetch bot name, resolve owner. Both the HTTP route and MCP tool call this.
- **`do_unbind()`** — symmetrical to `do_bind`. Added 2026-05-22 after
  the agent reported "Lark module currently has no unbind tool" when a
  user asked to disconnect via natural language. The cleanup logic
  used to live inline in `backend/routes/lark.py:unbind_lark_bot`; it
  is now hoisted here so the new `lark_unbind` MCP tool can call the
  exact same steps. Order: get credential (early `no_credential` if
  missing) → best-effort CLI profile remove → best-effort workspace
  cleanup → DB row delete (NOT best-effort) → bus channel reap for
  every `lark_*` channel the agent participated in (NOT best-effort).
  Best-effort steps swallow exceptions because keychain / workspace
  may already be partially gone from a half-failed bind; DB + bus
  cleanup must succeed or the next bind collides on a leaked row.
- **`resolve_owner()`** — looks up a Lark user by email via `_run_with_agent_id`,
  returns `(open_id, display_name)`.
- **`determine_auth_status()`** — pure function that interprets the
  lark-cli `auth status` output into one of 3 states: `bot_ready`,
  `user_logged_in`, or `not_logged_in`.

## Upstream / downstream

- **Upstream**: `backend/routes/lark.py`, `_lark_mcp_tools.py`.
- **Downstream**: `LarkCLIClient` (`config_init`, `_run_with_agent_id`),
  `LarkCredentialManager`.

## Gotchas

- `do_bind` uses `_cli.config_init()` (--profile) not `_run_with_agent_id`. This is
  intentional — manual bind provides app_id/secret directly, no workspace
  needed. Only Quick Setup (`config init --new`) uses HOME isolation.
- `resolve_owner` uses `_run_with_agent_id` which maps to --profile. The owner email
  lookup requires bot identity (tenant_access_token), not user identity.
- `do_unbind` imports `cleanup_workspace` lazily inside the function
  body to keep the module-level import surface stable — `_lark_workspace`
  pulls in OS-specific symlink logic that fails on import in some test
  environments without filesystem permissions.
