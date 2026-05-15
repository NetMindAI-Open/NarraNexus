---
code_file: src/xyz_agent_context/module/slack_module/_slack_credential_manager.py
stub: false
last_verified: 2026-05-09
---

## Why it exists

Owner of the ``channel_slack_credentials`` table. One row per agent.
Validates tokens at write time so a bad row never reaches the trigger,
and back-fills identity (``team_id`` / ``team_name`` / ``bot_user_id``)
from Slack's response so the rest of the system has the data it needs
without a second round-trip.

Mirror of ``LarkCredentialManager`` — same shape (bind / get / unbind /
list_active / public-view), same encryption-at-rest decision (base64,
not real crypto).

## Design decisions

- **``auth.test`` runs BEFORE persisting.** A pasted token that has
  been revoked, mistyped, or scoped wrong fails synchronously here
  instead of silently sitting in the DB until the trigger picks it up
  and the WS handshake fails 3 reconnect cycles later. The bind
  endpoint feels instant on success and instant on failure.
- **Token prefix sniff (``xoxb-`` / ``xapp-``).** Cheap pre-check that
  rejects obvious paste mistakes (swapped tokens, OAuth user tokens,
  etc.) before we burn an ``auth.test`` call.
- **Two tokens stored, both required.** ``bot_token`` (``xoxb-``) for
  Web API; ``app_token`` (``xapp-``) for Socket Mode. They serve
  different purposes — losing either breaks one half of the channel.
- **Base64 encoding at rest.** This is **NOT** encryption. Documented
  here and in the docstring so production deployments know to swap in
  KMS / Vault. Mirrors Lark's choice for consistency.
- **``SlackCredential`` is a plain dataclass, not frozen.** Lark uses
  the same shape. ``to_public_dict`` is the canonical sanitised view
  (NO tokens) — every API response and log line uses it.
- **Upsert keyed on ``agent_id``.** One agent = one Slack workspace
  binding. Re-binding overwrites; supports the dashboard "swap bot"
  flow without an explicit unbind step.
- **One bot can serve at most ONE agent.** ``bind()`` does an
  app-level pre-check that rejects with a friendly "already bound to
  agent X" error when the same ``(team_id, bot_user_id)`` is requested
  by a second agent. The DB also has a UNIQUE INDEX on the same tuple
  as the final guard against concurrent races. Why: Slack Socket Mode
  issues exactly one active WebSocket per ``app_token`` — two agents
  sharing a bot would race on the slot and one would silently lose
  events. The trust signal would also flip-flop (each agent has its
  own ``owner_user_id``).
- **Optional ``owner_email`` resolves owner identity.** When supplied
  at bind, ``users.lookupByEmail`` is called and ``owner_user_id`` /
  ``owner_name`` are persisted. A failed lookup logs a warning but
  does NOT fail the bind — the bot still works, just without the
  ``is_owner_interacting`` trust signal. The email itself is also
  stored so the dashboard can show what was tried.
- **``list_active`` returns enabled rows only.** ``enabled=0`` exists
  as a soft-disable flag for ops to silence a misbehaving bot without
  unbinding (preserving credential history).

## Upstream / downstream

- **Upstream**: REST routes (``backend/routes/slack.py``), MCP tools
  (``_slack_mcp_tools.py``), and the trigger's credential watcher
  (``slack_trigger.load_active_credentials``).
- **Downstream**:
  - ``SlackSDKClient.auth_test`` — validation surface during ``bind``.
  - ``AsyncDatabaseClient`` — table CRUD.
  - ``channel_slack_credentials`` table (declared in
    ``utils/schema_registry.py``).

## Gotchas

- The base64 decode runs on every ``get`` call — micro-cost but if
  this becomes hot, cache the decoded ``SlackCredential`` per agent
  (the trigger's credential watcher already does at the platform layer).
- ``bot_user_id`` is required after bind — we early-return when
  ``auth.test`` doesn't produce one. This shouldn't happen with a real
  ``xoxb-`` token, but Slack has been known to return partial responses.
- The ``enabled`` column stored as ``1`` / ``0`` int (SQLite booleans
  are ints). Never compare against ``True``/``False`` directly when
  reading from the DB — convert via ``bool(...)``.
- Tokens get logged at INFO level only as part of the success message
  (team name only). The ``SlackCredential.bot_token`` field carries
  decoded plaintext; do **NOT** add ``logger.debug(cred)`` anywhere.
