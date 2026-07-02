---
code_file: src/xyz_agent_context/module/narramessenger_module/_narramessenger_credential_manager.py
stub: false
last_verified: 2026-07-02
---

## Why it exists

CRUD for `channel_narramessenger_credentials` (one row per agent). Dataclass
`NarramessengerCredential` + `NarramessengerCredentialManager`, mirroring the
telegram credential manager.

## Design decisions

- **One secret only: `bearer_token`** (base64-encoded in DB, NOT encryption —
  same placeholder convention as lark/slack/telegram). v1 needs no Matrix
  access token because there is no Matrix client.
- **Fields beyond the token**: `backend_base_url` + `matrix_homeserver_url`
  (the two URLs), `matrix_user_id` (bot identity, unique), `nexus_principal_id`
  / `nexus_profile_id` (ids returned at connect), `bind_room_id`,
  `owner_matrix_user_id` / `owner_name` (trust signal), `connection_mode`
  (default `gateway`), `enabled`.
- **`upsert` writes the row directly** (no `getMe`-style validation API like
  Telegram); liveness is checked at runtime via `/status`.
- **`list_active()`** (`enabled=1`) is what the trigger watcher consumes;
  `set_enabled(False)` lets the trigger break a reconnect loop against a
  revoked bearer.
- `to_public_dict()` never includes the bearer token.

## Upstream / downstream

- **Used by**: the trigger (`list_active`, and — since 2026-07-02 —
  `update_owner` from `NarramessengerTrigger._maybe_claim_owner`, the X2/X3
  owner-auto-claim fix), the module (`get`), the MCP tools, and
  `scripts/seed_narramessenger_credential.py` (`upsert`).
- **Table**: `channel_narramessenger_credentials` (see `utils/schema_registry.py`).

## Gotchas

- `matrix_user_id` is UNIQUE — the same Matrix bot binds to at most one agent
  (two agents polling the same bearer would split invocations).
