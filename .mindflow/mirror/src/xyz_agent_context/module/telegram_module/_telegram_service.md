---
code_file: src/xyz_agent_context/module/telegram_module/_telegram_service.py
stub: false
last_verified: 2026-05-09
---

## Why it exists

Two stateless helpers — ``do_bind`` and ``do_test_connection`` — that
both REST routes (``backend/routes/telegram.py``) and MCP tools
(``_telegram_mcp_tools.py``) call into. Single source of truth for
"bind a Telegram bot to an agent" and "is the stored token still
valid against ``getMe``?".

Pattern mirrors ``lark_module/_lark_service.py`` and
``slack_module/_slack_service.py``. Lifting bind/test logic out of the
two call surfaces keeps them in lockstep — adding a side-effect
(emit metric, write audit row) at bind time happens here once.

## Design decisions

- **Thin pass-through to ``TelegramCredentialManager.bind``.**
  ``do_bind`` is a one-liner today; the future of this module is to
  hold the post-bind side-effects that REST and MCP both need
  (audit, metric, notification fan-out). Dedicated module reserves
  that seam.
- **``do_test_connection`` re-runs ``getMe`` against the stored
  credential.** A pure DB lookup wouldn't catch token revocation at
  @BotFather (``/revoke`` from the user). The live call is the only
  way to detect "you typed /revoke five minutes ago and we don't know
  yet" — the trigger only finds out when the next ``getUpdates``
  401s.
- **Failures are logged + returned as ``{"success": false, "error":
  ...}``, not raised.** Both call surfaces (REST and MCP) want a
  branchable dict, not exception handling.
- **Owner-username defaulting at signature, not in body.** Forces
  callers to be explicit when they DO supply it; defaulting in the
  function signature reads more naturally than a dict-unpacking dance.

## Upstream / downstream

- **Called by**: ``backend/routes/telegram.py`` (POST /bind, POST /test)
  and ``_telegram_mcp_tools.tg_bind / tg_status``.
- **Calls**: ``TelegramCredentialManager.bind / get``,
  ``TelegramSDKClient.get_me``.

## Gotchas

- ``do_test_connection`` opens a fresh ``TelegramSDKClient`` per
  call; closes it in ``finally``. Don't move the close into the
  try-block or token-revoked failures leak the session.
- The function set is intentionally minimal. Resist the urge to add
  ``do_unbind`` / ``do_get_status`` here — those are pure
  manager/CLI dispatch, no shared logic to extract yet.
