---
code_file: backend/routes/lark.py
stub: false
last_verified: 2026-06-01
---

## Why it exists

REST surface for the dashboard's Lark/Feishu binding flow — what
``frontend/src/components/awareness/LarkConfig.tsx`` calls when the
user pastes ``app_id`` / ``app_secret`` and walks through Bind →
Login → Complete → Test / Unbind. Seven endpoints, all mounted under
``/api/lark``. The Lark surface is larger than Slack's because Lark
adds an OAuth device-code login step that Slack doesn't have.

## Design decisions

- **Seven endpoints, shaped by Lark's two-phase auth.**
  - POST ``/bind`` — persist ``app_id`` / ``app_secret`` / ``brand``.
  - POST ``/auth/login`` — kick off OAuth with ``--no-wait``; returns
    an auth URL + device code for the user to authorize in a browser.
  - POST ``/auth/complete`` — finish the login with the device code
    from the previous ``--no-wait`` call.
  - GET ``/auth/status`` — read login state, syncing it back to the DB.
  - POST ``/test`` — fetch the bot's own info to prove the binding works.
  - POST ``/unbind`` — tear the binding down (see below).
  - GET ``/credential`` — sanitized view (NO ``app_secret``) for the UI.
  The device-flow split (``/auth/login`` then ``/auth/complete``) is the
  structural difference from ``slack.py`` — Slack validates tokens
  synchronously on bind, Lark needs a human browser round-trip in
  between, so the state lives across two calls keyed by the device code.
- **``/unbind`` delegates ALL cleanup to ``_lark_service.do_unbind``.**
  The route used to inline the cleanup (CLI profile removal, workspace
  directory wipe, DB record delete, and the ``bus_channel_members`` /
  ``bus_messages`` / ``bus_channels`` reap for every ``lark_`` channel).
  That logic now lives in ``do_unbind`` so the MCP tool ``lark_unbind``
  (``_lark_mcp_tools.py``, which calls ``do_unbind(mgr, agent_id, db)``)
  does the byte-identical thing. REST and natural-language unbind can no
  longer drift — the bus-channel reap is the part you must not let one
  path forget, which is exactly why it was centralized.
- **``/unbind`` keeps the legacy ``{"success": True}`` envelope.**
  ``do_unbind`` returns a richer result, but the route flattens it back
  to the old shape (and surfaces ``result["message"]`` on failure so the
  "No Lark bot bound to this agent." UX is preserved) specifically so
  ``LarkConfig.tsx`` needs no change. The service got richer; the wire
  contract didn't.
- **POST ``/unbind``, not DELETE.** Some proxies strip DELETE bodies,
  and the body carries ``agent_id``. Same rationale documented in
  ``slack.py:unbind_slack_bot``.
- **``/bind`` shares ``do_bind`` with the MCP path too.** Same
  REST-and-MCP-call-one-helper discipline as ``/unbind`` — the dashboard
  and an agent binding itself see identical envelopes for identical input.
- **Auth status is reconciled to the DB on read.** ``/auth/status`` runs
  the CLI ``auth status``, maps it through ``determine_auth_status``, and
  writes the result back via ``mgr.update_auth_status`` only when it
  changed — then echoes it as ``db_auth_status``. The CLI is the live
  truth; the DB column is a cache the UI can read without shelling out.
- **``/auth/complete`` opportunistically captures the bot name.** On
  success it calls ``GET /open-apis/bot/v3/info --as bot`` and stores
  ``app_name`` / ``name`` via ``mgr.update_bot_name``. Bot identity has
  no "self" user concept, so ``+get-user --as bot`` would fail without a
  ``--user-id`` — the bot-info API is the only handle on the name here.
- **Per-endpoint ownership check via ``_verify_agent_ownership``.**
  Local mode (no ``request.state.user_id``) skips enforcement; cloud
  mode requires the agent's ``created_by`` to match the JWT user_id.
  Same pattern as the rest of ``backend/routes``.
- **Pydantic ``pattern`` constraints at the boundary.** ``agent_id`` /
  ``app_id`` must match ``^[a-zA-Z0-9_\-]+$``; ``device_code`` matches
  ``^[a-zA-Z0-9_\-\.]{1,256}$``. Injection-style payloads are rejected
  before they reach the CLI runner or the DB. ``owner_email`` gets a
  cheap ``"@" in`` sanity check, not full RFC validation.

## Upstream / downstream

- **Upstream**:
  - Frontend ``api.bindLarkBot`` / ``larkAuthLogin`` /
    ``larkAuthComplete`` / ``getLarkAuthStatus`` / ``testLarkConnection``
    / ``unbindLarkBot`` / ``getLarkCredential`` (in
    ``frontend/src/lib/api.ts``).
  - FastAPI app router (registers this under ``/api/lark``).
- **Downstream**:
  - ``LarkCredentialManager`` for credential CRUD + auth-status / bot-name
    updates.
  - ``_lark_service`` helpers ``do_bind`` / ``do_unbind`` /
    ``determine_auth_status`` — the logic shared with the MCP tools.
  - ``LarkCLIClient`` (module-level ``_cli`` singleton) — every
    CLI-backed call goes through ``_run_with_agent_id`` (the V2
    workspace-based runner), so each agent's keychain / ``config.json``
    stays isolated under its own workspace.
  - ``get_db_client`` singleton via ``_get_db``.

## Gotchas

- Errors return ``{"success": false, "error": "..."}`` with HTTP 200,
  same envelope-as-payload convention as the rest of the codebase. The
  frontend branches on ``success`` — switching to non-200 status codes
  would break ``LarkConfig.tsx`` silently.
- Ownership failures are "permission denied" in the response body, not a
  403. Agent-ownership is application logic here, not an HTTP-layer
  concern.
- ``/auth/login`` is fire-and-poll by design: it returns ``--no-wait``,
  so a successful response means "auth URL issued", NOT "logged in".
  Login is only real after ``/auth/complete`` succeeds. Don't treat a
  200 from ``/auth/login`` as a connected state.
- ``do_unbind`` is the single owner of the ``lark_`` bus-channel reap. If
  you add a new side effect to unbind (extra table, extra workspace
  artifact), add it inside ``do_unbind`` — NOT in this route — or the
  MCP ``lark_unbind`` path will silently skip it.
- The CLI profile removal inside ``do_unbind`` is best-effort (the
  workspace may already be gone); don't tighten it into a hard failure
  or a re-unbind after a partial teardown will 500.
