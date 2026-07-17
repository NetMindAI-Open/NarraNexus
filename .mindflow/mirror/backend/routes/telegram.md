---
code_file: backend/routes/telegram.py
stub: false
last_verified: 2026-07-13
---

## 2026-07-13 — `/set-active` endpoint (activation)

Added `POST /set-active` (flip `enabled` without a re-bind) → **5 endpoints now** (was 4). Used to activate a bundle-imported (inactive) Telegram credential via `set_enabled`.

## Why it exists

REST surface for the dashboard's Telegram bind UX
(``frontend/src/components/awareness/TelegramConfig.tsx``). Mirrors
``backend/routes/slack.py`` and ``backend/routes/lark.py``: five endpoints, agent-ownership-checked, all delegating to the shared
``_telegram_service`` helpers so MCP and HTTP paths stay in lockstep.

## Design decisions

- **Five endpoints, intentionally minimal.**
  - ``POST /bind`` — validate token + persist
  - ``GET /credential`` — sanitised view (NO token)
  - ``POST /test`` — re-run ``getMe`` against stored token
  - ``DELETE /unbind`` — remove the row
  No "list bots", "edit metadata", "rotate token" — those are
  YAGNI for Phase 4.
- **``GET /credential`` returns ``to_public_dict()`` — the raw
  token is never serialized to HTTP.** Pydantic doesn't enforce
  this; the manager method does. If you bypass and return the
  raw ``TelegramCredential`` dataclass via FastAPI's auto-
  serialization, the token leaks. Keep the explicit
  ``mgr.get_public()`` call.
- **Agent-ownership check on every endpoint.**
  ``_verify_agent_ownership`` matches ``request.state.user_id`` (set
  by upstream auth middleware) against ``agents.created_by``. Without
  this any logged-in user could bind / unbind / read another user's
  Telegram credentials by guessing ``agent_id``.
- **agent_id pattern ``^[a-zA-Z0-9_\-]+$`` — defense-in-depth
  against SQL/path injection.** Same shape as the Slack/Lark routes.
  The ORM parameterises queries already; this is a belt-and-braces
  guard at the edge.
- **Returns ``{"success": bool, ...}`` envelopes, not HTTP error
  codes.** Consistent with the rest of the project's REST surface
  (``api.ts`` expects this shape). Auth failures still come back as
  ``200 OK`` with ``success=false`` + an explanatory error string.
- **Logger lines are explicit at bind / unbind, silent on test /
  get.** Test and credential-read are high-frequency; only state-
  changing actions are worth audit-log noise.
- **Lazy import of ``get_db_client``.** Avoids module-level import
  cycles between FastAPI app boot and the database backend factory.

## Upstream / downstream

- **Mounted at**: ``/api/telegram/*`` from ``backend/main.py``.
- **Calls**: ``TelegramCredentialManager``,
  ``_telegram_service.do_bind / do_test_connection``.
- **Consumed by**: ``frontend/src/components/awareness/TelegramConfig.tsx``
  via ``frontend/src/lib/api.ts`` (``bindTelegramBot``,
  ``getTelegramCredential``, ``testTelegramConnection``,
  ``unbindTelegramBot``).

## Gotchas

- ``GET /credential`` returning a raw token would silently break
  the security model — there's no test pinning this beyond
  ``to_public_dict()``'s implementation. Manual eyeball on any edit.
- ``request.state.user_id`` depends on the auth middleware being
  installed in front of this router. Without it
  ``_verify_agent_ownership`` returns ``None`` (no error) and the
  endpoint runs unauthenticated. The check belongs at the app level;
  this file trusts it.
- Bind logs ``owner=...`` but never the token. Don't add the token
  to log lines for "debugging" — it's a one-time secret.
