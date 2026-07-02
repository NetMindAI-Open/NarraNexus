---
code_file: src/xyz_agent_context/module/discord_module/_discord_service.py
stub: false
last_verified: 2026-06-16
---

## Why it exists

Shared bind / test helpers so REST routes
(``backend/routes/discord.py``) and MCP tools (``_discord_mcp_tools.py``)
call one place. Pattern mirrors ``slack_module/_slack_service.py``.

## Design decisions

- **``do_bind`` / ``do_test_connection`` are thin delegations** to the
  credential manager + SDK client, plus ``_friendly_discord_error`` to
  turn raw codes (``unauthorized`` / ``forbidden`` / ``not_found`` /
  ``rate_limited``) into actionable English for first-time users.
- **``do_test_connection`` refreshes the stored bot identity** — owners
  can rename the bot in the Developer Portal after binding, so a Test
  also calls ``update_bot_identity`` to keep ``bot_username`` current.

## Upstream / downstream

- **Upstream**: ``DiscordCredentialManager``, ``DiscordSDKClient``.
- **Downstream**: ``backend/routes/discord.py`` and ``_discord_mcp_tools``
  (both bind / status paths).

## Gotchas

- ``_friendly_discord_error`` falls through with the raw code for
  unmapped errors — intentional; don't swallow unknown codes.
