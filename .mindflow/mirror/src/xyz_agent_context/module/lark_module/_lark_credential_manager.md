---
code_file: src/xyz_agent_context/module/lark_module/_lark_credential_manager.py
stub: false
last_verified: 2026-04-22
---

## Why it exists

CRUD layer for the `lark_credentials` table.  Every file that needs
to read/write Lark bot credentials goes through this manager rather
than touching the DB directly.

## Design decisions

- **Dataclass, not Pydantic** — `LarkCredential` is a plain
  `@dataclass` because it is internal-only and does not need
  serialization validation.
- **`_encode_secret` / `_decode_secret`** — base64 encoding (NOT
  encryption).  Naming was changed from `_encrypt/_decrypt` to avoid
  misleading future readers.  A TODO remains for AES-based encryption
  in production/cloud mode.
- **DB column still named `app_secret_encrypted`** — the column name
  is kept for backward compatibility with existing databases; the
  Python field was renamed to `app_secret_encoded`.
- **`permission_state` is a JSON blob**, not a set of columns. Key
  schema lives in a docstring on the `permission_state` field; the
  three-click fields (`admin_request_*`, `admin_approved_at`,
  `user_authz_*`, `user_oauth_completed_at`, etc.) can evolve without
  DB migrations.
- **`current_click_stage()` is the single source of truth** for the
  three-click state machine. `get_instructions`, `lark_permission_advance`
  guards, and `lark_status` returns all route through this method.
  Strictly derived from DB fields — never from user's literal words.

## Upstream / downstream

- **Upstream**: `lark_module.py`, `_lark_mcp_tools.py`, `lark_trigger.py`,
  `backend/routes/channels/lark.py`.
- **Downstream**: `AsyncDatabaseClient` via `self.db`.

## Gotchas

- `save_credential` does a read-then-write (SELECT then INSERT/UPDATE).
  Under rapid concurrent bind requests for the same agent_id, a race
  is theoretically possible.  The UNIQUE index on agent_id provides a
  safety net.
