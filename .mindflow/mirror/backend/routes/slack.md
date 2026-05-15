---
code_file: backend/routes/slack.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

REST surface for the dashboard's Slack binding flow — what
``frontend/src/components/awareness/SlackConfig.tsx`` calls when the
user pastes tokens and clicks Bind / Test / Unbind. Four endpoints,
all mounted under ``/api/slack``.

## Design decisions

- **Four endpoints, deliberately small surface.**
  - POST ``/bind`` — paste tokens, validate, persist.
  - GET ``/credential`` — sanitized view (NO tokens) for the UI.
  - POST ``/test`` — re-run ``auth.test`` to catch revocation.
  - DELETE ``/unbind`` — remove the row.
  Slack's bind is atomic (no OAuth device-flow polling), so unlike Lark
  there's no ``/poll`` endpoint and no ``/install`` redirect URL.
- **GET ``/credential`` returns ``mgr.get_public(...)`` — sanitized.**
  Tokens never leave ``SlackCredentialManager``. The frontend only
  needs ``team_id`` / ``team_name`` / ``bot_user_id`` / ``enabled``
  to render the connected state. Keeping tokens server-side means an
  XSS or browser memory dump can't lift them.
- **All endpoints share ``do_bind`` / ``do_test_connection`` from
  ``_slack_service``.** REST and MCP tools call the same helpers so
  the dashboard and the agent see identical envelopes for the same
  inputs. Drift between the two paths is the bug we don't want.
- **Per-endpoint ownership check via ``_verify_agent_ownership``.**
  Local mode (no JWT in ``request.state``) skips enforcement; cloud
  mode requires the agent's ``created_by`` to match the JWT user_id.
  Same pattern as the rest of ``backend/routes`` — keeps single-tenant
  dev painless without weakening cloud-tenant isolation.
- **Pydantic schemas with ``pattern`` constraints on ``agent_id``.**
  ``^[a-zA-Z0-9_\-]+$`` blocks injection-style payloads at the
  framework boundary before they reach the DB layer.
- **Token length capped at 512.** Real Slack tokens are well under
  that; the cap is just defence-in-depth against pathological inputs.

## Upstream / downstream

- **Upstream**:
  - Frontend ``api.bindSlackBot`` / ``getSlackCredential`` /
    ``testSlackConnection`` / ``unbindSlackBot`` (in
    ``frontend/src/lib/api``).
  - FastAPI app router (registers this under ``/api/slack``).
- **Downstream**:
  - ``SlackCredentialManager`` for CRUD.
  - ``do_bind`` / ``do_test_connection`` for shared logic.
  - ``get_db_client`` singleton.

## Gotchas

- Errors return ``{"success": false, "error": "..."}`` with HTTP 200.
  The frontend reads ``success`` to branch — switching to non-200
  status codes would break ``SlackConfig.tsx`` silently. Stay
  consistent with the rest of the codebase's envelope-as-payload
  convention.
- The auth-ownership check is "permission denied" via response body,
  not 403. Same reason as above — the rest of the codebase treats
  agent-ownership as application logic, not HTTP layer.
- ``BindRequest.bot_token`` / ``app_token`` are passed through
  unmodified (after Pydantic length / type validation) — the
  ``xoxb-`` / ``xapp-`` prefix sniff happens in
  ``SlackCredentialManager.bind``. Don't add a second prefix check
  here; it would race the upstream rename if Slack ever changes the
  scheme.
