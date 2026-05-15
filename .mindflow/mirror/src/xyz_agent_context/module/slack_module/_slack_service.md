---
code_file: src/xyz_agent_context/module/slack_module/_slack_service.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Single source of truth for Slack ``bind`` and ``test_connection``
behaviour. Both REST routes and MCP tools call into here — without
this seam they would drift (one path adds a field, the other
forgets, agents and dashboard see different envelopes).

Pattern lifted directly from ``lark_module/_lark_service.py``.

## Design decisions

- **Helpers, not a class.** No state to encapsulate; an
  ``async def`` shared by two callers is the simplest fit. Adding a
  service class would force constructor wiring on both call sites for
  no benefit.
- **``do_bind`` is a one-liner forwarding into the manager.** Today
  it adds nothing on top of ``SlackCredentialManager.bind`` — it
  exists so future cross-cutting policy (audit log, owner-rate-limit
  on bind, telemetry) lands in one place that both REST and MCP go
  through.
- **``do_test_connection`` re-runs ``auth.test``.** Catching token
  revocation that wouldn't show up in a pure DB lookup is the entire
  point of the "Test" button on the dashboard. Re-using
  ``SlackSDKClient.auth_test`` keeps the validation logic identical
  to the bind path.
- **Returns the standard envelope.** ``{success, error?, data?}`` —
  same shape REST routes and MCP tools already emit. Callers can
  forward the dict unchanged.

## Upstream / downstream

- **Upstream**:
  - ``backend/routes/slack.py`` — POST /bind and POST /test endpoints.
  - ``_slack_mcp_tools.py`` — ``slack_bind`` and ``slack_status``
    tools.
- **Downstream**:
  - ``SlackCredentialManager.bind`` / ``.get``.
  - ``SlackSDKClient.auth_test``.

## Gotchas

- Errors here surface as plain strings prefixed with ``"slack
  auth.test failed: "`` plus the Slack error code. Don't change the
  prefix without auditing the dashboard's error-display copy.
- ``do_test_connection`` returns a different ``data`` shape from
  ``do_bind`` (adds ``bot_name``, omits ``enabled`` etc.). The
  dashboard knows; if a third caller appears, prefer normalising
  rather than letting the shapes drift further.
