---
code_file: src/xyz_agent_context/module/lark_module/lark_cli_client.py
stub: false
last_verified: 2026-05-21
---

## 2026-05-21 — `get_user` defaults to `--as user`

`get_user` now runs `contact +get-user --as user` (was `--as bot`). The
bot tenant token lacks `contact:user.base:readonly`, so `--as bot`
returns only `open_id`/`union_id` with no `name` for **every** sender —
which made `LarkTrigger.resolve_sender_name` fall back to "Unknown" for
everyone (and the agent then guessed names from its roster). Each
lark-configured agent already holds the owner's user token in its
isolated HOME (from the three-click auth), and that token *can* read
names, so we resolve through it. `identity="bot"` stays selectable for
callers that genuinely want the app identity.

## Why it exists

Unified async wrapper around every `lark-cli` subprocess call. Turns the
CLI into a single-function API: call `_run_with_agent_id(args, agent_id)`
and the client handles credential lookup, workspace hydration, and HOME
isolation transparently.

## Design decisions

- **DB is source of truth, workspace is derived.** Every agent's Lark
  state (app_id, plain app_secret, profile name, brand) lives in
  `lark_credentials`. The per-agent workspace (`~/.narranexus/lark_workspaces/<id>/`)
  is a view that can be rebuilt from DB at any time.
- **`_ensure_hydrated(cred)` is idempotent.** Before every agent-scoped
  call we check `workspace/.lark-cli/config.json` — if it already lists
  `cred.app_id`, we skip; otherwise we rebuild by running
  `lark-cli config init --app-id X --app-secret-stdin --name Y --brand Z`
  with HOME=workspace. Plain secret flows DB → stdin → CLI, never
  touches args.
- **Single workspace, single profile, no `--profile` flag.** Because
  each workspace contains exactly one active profile, we never need
  `--profile` on subsequent commands. This matches how a single-machine
  user naturally uses `lark-cli`.
- **Lazy migration for legacy manual binds.** Pre-refactor manual binds
  had `workspace_path=""` in DB. On first call, `_run_with_agent_id`
  computes the path, persists it, and hydrates — no startup migration
  script needed.
- **`_run_with_home` kept for one special case** — `config init --new`
  during `lark_setup` creates the credential itself, so it runs before
  any DB row exists and bypasses hydration.
- **`shell=False` everywhere**, secrets via `stdin_data`, timeout kills
  the subprocess. All unchanged from V1.

## Upstream / downstream

- **Upstream**: every `_lark_mcp_tools.py` tool, `_lark_service.do_bind`
  (which uses `_run_with_agent_id` to verify credentials by hitting bot
  info), `lark_trigger.py` (for `get_user`, bot open_id lookup,
  `_resolve_sender_name`), `lark_context_builder.py` (`list_chat_messages`),
  `lark_module.py` (`send_message`), `backend/routes/lark.py` (unbind),
  `backend/routes/auth.py` (delete_agent).
- **Downstream**: `lark-cli` binary, `_lark_workspace.py` (paths + HOME
  env), `_lark_credential_manager.py` (cred fetch, lazy migration
  persistence).

## Gotchas

- Hydration triggers a real `config init` subprocess. First call on a
  cold workspace can take a couple of seconds. Subsequent calls are fast
  (idempotence check is a single file read + JSON parse).
- If DB has no plain secret (agent-assisted setups before
  `lark_enable_receive`), hydration fails deterministically with an
  actionable error telling the caller to complete Phase 2.
- Debug logs include the full `lark-cli` command. Secrets are passed via
  `stdin_data`, so they never appear in logs — keep it that way.
- `_exec_lark_cli` is private; external callers must go through
  `_run_with_agent_id` (typical), `_run_with_home` (config init --new),
  or the business methods (`send_message`, `get_user`, etc.).
