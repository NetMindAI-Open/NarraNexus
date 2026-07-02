---
code_file: src/xyz_agent_context/module/discord_module/_discord_credential_manager.py
stub: false
last_verified: 2026-06-16
---

## Why it exists

CRUD for the ``channel_discord_credentials`` table — one row per agent.
``bind`` validates the token via ``GET /users/@me`` and optionally
resolves the owner's display name from a supplied numeric Discord user
id. Sibling of ``_telegram_credential_manager.py``.

## Design decisions

- **Owner is a NUMERIC user id, resolved eagerly at bind.** Discord
  usernames aren't stable and the REST API resolves users by id, so the
  trust signal keys on ``owner_user_id`` directly. The name is fetched
  once at bind via ``get_user`` (best-effort; missing name doesn't fail
  the bind). No Telegram-style first-DM late-resolution dance is needed.
- **No token regex.** Discord bot tokens have no single stable shape
  across eras, so ``GET /users/@me`` is the sole authority; only a
  non-numeric ``owner_user_id`` is rejected pre-flight.
- **Bot uniqueness on ``bot_user_id`` alone** (app-level check + DB
  UNIQUE index). One token → one Gateway session; two agents sharing it
  would fight over the single session and flip-flop the trust signal.
- **Token base64-encoded at rest** (``_encode_token`` / ``_decode_token``)
  — matches the lark/slack/telegram convention. NOT encryption; a
  production deploy should swap in KMS-backed crypto.
- **``set_enabled``** lets the trigger flip ``enabled=0`` on a permanent
  auth failure without deleting the row (same as Slack/Telegram).

## Upstream / downstream

- **Upstream**: ``DiscordSDKClient`` (auth + owner lookup),
  ``AsyncDatabaseClient``.
- **Downstream**: consumed by ``discord_module`` (get_credential),
  ``discord_trigger`` (load_active / set_enabled), ``_discord_service``,
  ``_discord_mcp_tools``, and ``backend/routes/discord.py``.

## Gotchas

- ``to_public_dict`` is the only safe shape for API responses / logs —
  it omits the token. The raw ``bot_token`` lives only on the dataclass.
- The table is registered in ``utils/schema_registry.py`` as
  ``channel_discord_credentials`` with a UNIQUE index on both
  ``agent_id`` and ``bot_user_id``.
