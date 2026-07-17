---
code_file: backend/routes/discord.py
stub: false
last_verified: 2026-07-13
---

## 2026-07-13 — `/set-active` endpoint (activation)

Added `POST /set-active` (flip `enabled` without a re-bind) → **5 endpoints now** (was 4). Used to activate a bundle-imported (inactive) Discord credential via `set_enabled`.

## Why it exists

REST surface for the dashboard's Discord bind UX
(``frontend/src/components/awareness/DiscordConfig.tsx``). Mirrors
``backend/routes/telegram.py`` / ``slack.py``: five endpoints
(bind / credential / test / unbind), agent-ownership-checked, all
delegating to the shared ``_discord_service`` helpers so the MCP and HTTP
bind paths stay in lockstep.

## Design decisions

- **``owner_user_id`` (numeric) replaces Telegram's ``owner_username``.**
  Discord trust keys on the numeric user id; the bind request carries it
  optionally.
- **POST for unbind** (not DELETE) — some proxies strip DELETE bodies;
  same rationale as slack.py.
- **Local-mode auth caveat** inherited from the sibling routes: without
  the JWT middleware, ``request.state.user_id`` is unset and the routes
  are effectively unauthenticated (dev only).

## Upstream / downstream

- **Upstream**: ``DiscordCredentialManager`` + ``_discord_service``
  (do_bind / do_test_connection).
- **Registered in**: ``backend/main.py`` under ``/api/discord``.
- **Downstream consumer**: ``DiscordConfig.tsx`` via the five
  ``api.*DiscordBot`` / ``*DiscordConnection`` methods in
  ``frontend/src/lib/api.ts``.

## Gotchas

- The MCP tools (``discord_bind`` / ``discord_unbind``) and these routes
  are TWO entry points to the same credential manager — keep both in mind
  when changing bind semantics.
