---
code_file: src/xyz_agent_context/module/lark_module/_lark_service.py
last_verified: 2026-05-27
stub: false
---

# _lark_service.py — shared Lark bind / owner / status helpers

## Why it exists

Business logic for Lark binding that must NOT live in the API layer
(`backend/routes/lark.py`) to avoid circular imports: both the HTTP
route AND the MCP tool layer (`_lark_mcp_tools.py`) call into here.
The two surfaces share the same `do_bind`, `resolve_owner`,
`determine_auth_status` primitives.

## Upstream / Downstream

- **Called by**: `backend/routes/lark.py` (HTTP bind), `_lark_mcp_tools.py`
  (agent-driven bind / diagnostic).
- **Calls**: `_lark_credential_manager` (DB), `lark_cli_client` (subprocess),
  `_lark_workspace` (HOME isolation), `_lark_error_translator` (user-facing
  errors).

## Design decisions

**`do_bind` is DB-first.** Save the credential row before verifying with
lark-cli, then verify by triggering a bot-info lookup that hydrates the
workspace via `config init`. Rollback on failure so DB and workspace stay
consistent. The reason `auth status` is used for credential verification
(rather than `get-user`) is documented inline — bot identity has no
"current user" concept.

**`brand` MUST be explicit.** Caller is expected to have ASKED the user
"Feishu or Lark?" — we can't auto-detect because both platforms accept
`cli_`-prefixed App IDs and both `tenant_access_token` endpoints
cross-route via redirect. Only the WebSocket subscriber enforces domain
strictly (error 1000040351 "Incorrect domain name"). By the time we see
that error the user is already bound, so silent brand mismatch is a
known pain point (see `_lark_error_translator` for the friendly
translation, and B.1 work for the runtime auth_status detection path).

## 2026-05-27 — structured `error_detail` on bind failure

`do_bind` now passes any verification failure through
`_lark_error_translator.translate()` and returns a `error_detail` dict
alongside the legacy `error` string. `error` stays as before for
backward-compat with older callers (MCP tool, tests); `error_detail`
carries `{code, severity, title, message, action_hint, console_url,
raw_message}` for the frontend to render as a structured card. See
the translator module for the lookup table and rationale.

## Gotchas

- `do_bind` returns `{success, error[, error_detail]}` — callers must
  not assume `error_detail` is present (it's only populated for
  verification failures via lark-cli, not for the early validation
  errors like "brand invalid" or "agent already bound").
- The `same_app` check rejects re-binding the same App ID to a
  different agent. This is intentional — one Lark app = one bot user;
  reusing it across agents would cross-wire message routing.
