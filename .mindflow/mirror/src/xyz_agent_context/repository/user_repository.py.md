---
code_file: src/xyz_agent_context/repository/user_repository.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — upsert_netmind_user upgrades a pre-existing local row (B4)

The UPDATE path now backfills `user_type="individual"` when the existing row is
not already individual. On a local dual-mode install a user can first exist as a
pure-local `"local"` username user and later log in with their Power account;
without this upgrade `is_power_account()` ([[power_account]]) would keep denying
them the billing panel. Only upgrades, never clobbers an already-individual row.

## 2026-06-12 — get_display_name: the single user_id→human-name resolver

New `get_display_name(user_id) -> str`: returns the user's `display_name`, or
the `user_id` itself when there is no display_name / no such user / `user_id` is
falsy. Never raises (lookup failure falls back to the id). This is the ONE DRY
place every prompt path resolves an opaque user_id to a human name, so the LLM
never sees a raw NetMind userSystemCode (32-hex) as a person. Consumers:
[[basic_info_module.py]] (creator_name / current_speaker_name),
[[_job_context_builder.py]] (execution_identity / task_creator),
[[message_bus_trigger.py]] (owner_name), and the narrative
[[prompt_builder.py]] (USER / PARTICIPANT actors).

## 2026-06-11 — upsert_netmind_user for NetMind login (Phase 1 user-system unification)

New `upsert_netmind_user(user_system_code, email, display_name) -> (User, is_new)`. NetMind login has no registration step: the first verified login lazily creates the local row (user_id = NetMind userSystemCode, user_type=individual, role left to the DB default 'user'); later logins mirror email/display_name drift and bump last_login_time. Incoming None never clobbers existing fields. Caller is POST /api/auth/netmind-login in backend/routes/auth.py.

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
