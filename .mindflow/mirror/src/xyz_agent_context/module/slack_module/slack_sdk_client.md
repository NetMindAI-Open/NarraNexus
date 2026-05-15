---
code_file: src/xyz_agent_context/module/slack_module/slack_sdk_client.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

The single chokepoint where ``slack_sdk`` is imported. Every other file
in ``slack_module/`` talks to Slack through this thin async wrapper —
that gives us one place to swap the SDK out, add retries, or instrument
calls without touching the trigger / module / MCP tools.

Encapsulates the runtime calls Slack channel code needs:
``auth.test`` (validate + identity discovery), ``chat.postMessage``,
``users.info``, ``conversations.history`` / ``replies``, plus a
generic ``api_call`` dispatcher that backs the agent-facing
``slack_cli`` tool.

## Design decisions

- **One SDK import, one boundary.** The entire rest of the package
  imports types from this file (``SlackSDKClient``, ``SlackSDKError``)
  rather than reaching into ``slack_sdk.errors`` / ``slack_sdk.web``
  directly. If we ever switch SDK or vendor it, only this file
  changes.
- **``SlackSDKError`` carries ``.code``, not just a message.** Callers
  branch on ``invalid_auth`` vs ``channel_not_found`` vs
  ``rate_limited`` to decide what user-facing message to show. Making
  the upstream Slack error code a first-class field saves every caller
  from string-matching messages.
- **``api_call`` does NOT raise.** It returns Slack's native envelope
  ``{"ok": false, "error": "..."}`` on failure. Reasoning: this method
  backs the agent-facing ``slack_cli`` MCP tool — agents are far better
  at reading the envelope (it's documented in every per-method skill
  doc) than at handling exceptions inside tool calls.
- **``send_message`` / ``auth_test`` DO raise.** They're internal call
  sites where the calling code already wraps in try/except and wants
  to branch on the code. Different error contract from ``api_call`` is
  intentional — internal helpers signal failure via exception, the
  agent-facing dispatcher signals via envelope.
- **``web`` property exposes the underlying ``AsyncWebClient``.** Only
  ``SlackTrigger.connect`` needs raw access (the Socket Mode client
  binds the same WebClient). Hiding it would force a leakier API.
- **``users_info`` failure returns ``{}``, not raises.** Display-name
  resolution is best-effort — falling back to the raw ``U...`` id is
  fine UX. We don't want a hiccup here to abort message processing.
- **One client instance per credential / call site.** ``slack_sdk``
  clients are cheap; sharing across agents would risk bot_token mixup.

## Upstream / downstream

- **Upstream**: anything in ``slack_module/`` (trigger, module,
  context builder, MCP tools, credential manager) that hits Slack.
- **Downstream**: ``slack_sdk.web.async_client.AsyncWebClient`` and
  ``slack_sdk.errors.SlackApiError``. Single point of contact.

## Gotchas

- ``SlackApiError.response`` may be ``None`` on transport errors
  (DNS, TLS). The ``code = (e.response.get(...) if e.response else "")``
  pattern is load-bearing — drop the guard and we crash on outage.
- ``api_call`` swallows all exceptions and returns a synthetic
  ``client_exception:<TypeName>`` envelope. That's deliberate for the
  ``slack_cli`` path but means real bugs (e.g. accidental
  ``TypeError`` in client code) hide as "API errors" — the
  ``logger.exception`` in that branch is the only signal.
- Tokens are passed in plaintext to ``AsyncWebClient(token=...)``.
  Storage is base64'd on disk (see ``_slack_credential_manager.py``);
  in-memory once decoded.
