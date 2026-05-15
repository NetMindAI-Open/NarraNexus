---
code_file: src/xyz_agent_context/module/telegram_module/_telegram_credential_manager.py
stub: false
last_verified: 2026-05-11
---

## Why it exists

CRUD layer for ``channel_telegram_credentials``. One row per agent.
Validates the bot token at bind time, defensively detaches any prior
webhook (so subsequent long-poll won't 409), optionally resolves the
owner @username to an immutable user_id, and persists the row with the
token base64-encoded.

Mirrors Slack's ``_slack_credential_manager.py`` end-to-end. Three
deltas captured here so the structural symmetry stays load-bearing.

## Design decisions

- **No ``team_id``. Bot uniqueness keys on ``bot_user_id`` alone.**
  Telegram is single-tenant per bot — there is no workspace concept.
  Slack needed ``(team_id, bot_user_id)`` because the same workspace
  might have multiple bots; on Telegram each bot IS the workspace.
- **App-level uniqueness pre-check before insert.** DB ``UNIQUE
  INDEX`` on ``bot_user_id`` is the final guard, but we look up
  ``existing_other`` before inserting so we can return a friendly
  error ("This bot is already bound to agent X — unbind it first or
  create a new bot via @BotFather") instead of a raw ``IntegrityError``.
  Phase 3 lesson #5: bot uniqueness MUST be enforced; concurrent races
  otherwise produce flip-flopping trust signals.
- **Defensive ``deleteWebhook`` before ``getMe``.** Bind is the only
  natural place to do this once. If a previous owner of this bot left
  a webhook configured, ``getUpdates`` would 409 forever afterward.
  The trigger's runtime 409-retry handles edge cases; the bind flow
  handles the common case.
- **Owner resolution via ``getChat("@handle")``.** The only public
  Telegram lookup that returns numeric ``user_id`` from a username.
  We store both ``owner_username`` (display) and ``owner_user_id``
  (immutable comparator) — the latter is what
  ``is_owner_interacting`` checks at runtime, because users can
  change their @handle but not their numeric id.
- **Failed owner lookup persists empty owner fields, never aborts
  bind.** A typo in the @username, or a username that doesn't exist,
  or a privacy-restricted account — all logged and dropped, the bind
  itself succeeds. Without owner fields the trust signal is OFF and
  every Telegram sender is treated as untrusted (documented in
  ``telegram_module.get_instructions``). Better than refusing to bind
  the bot.
- **Token base64-encoded at rest, decoded on load.** Same shape as
  Slack. Encoding != encryption — at-rest encryption is out of scope
  for Phase 4 (database is local SQLite or backed-up MySQL with
  filesystem-level protections). Encoding keeps casual ``less`` /
  ``SELECT *`` from bleeding the token to a screenshot.
- **``to_public_dict`` strips ``bot_token``.** This is what the
  ``GET /credential`` REST route returns. The token NEVER leaves
  ``TelegramCredential.bot_token`` (in-memory) or
  ``bot_token_encoded`` (DB column).
- **``list_active`` filters on ``enabled=1`` only.** Soft-disable
  semantics not yet wired to UI but the column exists for it.

## Upstream / downstream

- **Reads / writes**: ``channel_telegram_credentials`` table
  (registered in ``utils/schema_registry.py``).
- **Calls**: ``TelegramSDKClient.get_me / get_chat / delete_webhook``.
- **Used by**: ``_telegram_service.do_bind / do_test_connection``,
  ``telegram_trigger.load_active_credentials``,
  ``telegram_module.get_credential``,
  ``_telegram_mcp_tools.tg_cli / tg_status / tg_unbind``,
  ``backend/routes/telegram.py``,
  ``backend/routes/auth.py:delete_agent`` (via the channel cleanup
  registry walking ``ChannelModuleBase`` subclasses).

## Gotchas

- ``getChat("@username")`` requires the user to have a public
  username AND not be in a strict privacy mode. Failure is silent
  (logged at WARN, bind continues). The user must check
  ``credential.owner_user_id`` after bind to know it worked.
- Token-format check is ``":" in bot_token`` only. A malformed-but-
  contains-colon string passes our gate and fails at ``getMe``.
  That's fine — getMe's error message is clearer than a synthetic
  client-side regex error.
- Removing the defensive ``deleteWebhook`` will silently break any
  bind on a bot that previously had a webhook set. The runtime 409
  recovery in the trigger papers over this for already-bound bots
  but a brand-new bind would never even see the trigger.

## update_owner — late owner resolution

Added 2026-05-11 after discovering that Telegram's ``getChat`` API
**does not accept @username for regular user accounts** (only for
supergroups / channels / bots). At bind time the ``getChat("@handle")``
call almost always returns ``chat_not_found`` for a user @username,
even though the user is real and has DM'd the bot before.

The canonical resolution path is now in ``TelegramTrigger._process_message``:
when the FIRST DM arrives whose ``from.username`` matches the stored
``owner_username``, the trigger calls ``update_owner()`` to populate
``owner_user_id`` + ``owner_name``. ``bind()`` still attempts the
``getChat`` for completeness (in case the @handle is actually a public
channel / supergroup) but failure is benign and demoted from WARNING
to INFO. Security is preserved by the username-as-lock invariant — a
stranger can't claim ownership because their ``from.username`` won't
match.
