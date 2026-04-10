---
code_file: src/xyz_agent_context/repository/user_repository.py
last_verified: 2026-04-10
stub: false
---

# user_repository.py

## Why it exists

`UserRepository` manages the `users` table. Users are the humans (and potentially bots) that interact with agents. The repository provides standard CRUD plus timezone management and soft-delete support. User records are foundational — they are referenced by messages, inbox entries, instances, and the auth layer.

## Upstream / Downstream

Auth routes call `get_user()` on every request to verify identity and load user state. The user management API calls `add_user()` and `update_user()`. `AgentRuntime` calls `update_last_login()` on successful authentication. The timezone API route calls `update_timezone()`. `JobTrigger` calls `get_user_timezone()` to format scheduled times in the user's local timezone for prompts.

## Design decisions

**`id_field = "id"`**: same mismatch pattern. `get_user()` queries with `BINARY user_id = %s`. The `BINARY` keyword enforces case-sensitive comparison — `UserRepository` explicitly wants `"Alice"` and `"alice"` to be different users.

**All update methods use `BINARY user_id = %s`**: `update_user()` and `delete_user()` both use `BINARY user_id` in their WHERE clauses. This is correct and intentional — user IDs are case-sensitive.

**Soft delete via `UserStatus.DELETED`**: `delete_user(soft_delete=True)` sets `status = "deleted"`. The user row is retained. All foreign-key-like references (messages, events, instances) remain valid. Hard delete (`soft_delete=False`) physically removes the row — use with caution.

**`get_user_timezone()` returns `"UTC"` as default**: if the user does not exist (or exists but has no timezone set), the method returns `"UTC"` rather than raising. This prevents timezone-related errors from propagating into job scheduling.

## Gotchas

**Case sensitivity in `get_user()`**: the `BINARY user_id = %s` comparison is case-sensitive at the database level. If the user registered with ID `"Alice"` and the lookup passes `"alice"`, the query returns `None`. This is correct behavior but can cause confusion in development environments where user IDs might be created inconsistently.

**`UserStatus.BLOCKED` and `UserStatus.INACTIVE`** exist in the enum but there is no code in the auth flow that checks for them. If you set a user's status to `BLOCKED`, they can still log in unless the auth layer is updated to reject those statuses.

**`metadata` is stored as JSON string**: `_entity_to_row()` serializes via `json.dumps()` only if `metadata is not None`. If you pass `metadata={}` (empty dict), it will be serialized as `"{}"` and stored, which will deserialize correctly. But `None` metadata stays as NULL in the database.

## New-joiner traps

- `UserRepository.update_user()` (and `get_user()`) use the same `BINARY user_id` pattern. If you write a query that uses `user_id = %s` (without `BINARY`) in a context where the collation is case-insensitive (common MySQL default), you may get spurious matches. The repository methods are safe; ad-hoc queries are not.
- `UserStatus` is `str, Enum`, so `UserStatus.ACTIVE == "active"` is `True`. But `_row_to_entity()` constructs `UserStatus(row.get("status", "active"))`. If the database contains a typo (e.g., `"Active"` with capital A), `UserStatus("Active")` will raise `ValueError`. Be careful with manual database edits.
