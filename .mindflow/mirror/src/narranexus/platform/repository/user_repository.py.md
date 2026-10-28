---
code_file: src/narranexus/platform/repository/user_repository.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 (review 修正) — 新契约要有测试守着，否则只是换了个地方的同一个 bug

上一条把兜底收进了 `get_user_timezone`,但**没有任何测试锁住新契约**——把实现改回
`if user: return user.timezone`,全量 6468 条依旧全绿。

这跟这次修的 bug 是**同一个形状**：当时的问题就是"docstring 承诺了实现没做的事"。只改实现
不加测试，等于把下一次复发的条件原封不动留着——有人做 repository 层清理、或觉得多包一层
`resolve_timezone` 是多余开销，改回一行，CI 放行，然后 `instance_sync_service` 重新开始把
非法时区串**持久化**进 job 的 `trigger_config`,几个月后表现为"某几个 job 不再触发"。

`tests/repository/test_user_repository_timezone.py` 打三个分支：用户不存在、存的是空串、
存的是非 IANA 串。已用改回旧实现的方式验证过 3 条会红。

两个写法上的坑（都踩过一次才写对）：

- 断言必须是**精确的 `"UTC"` 字面量**,不能写 `is_valid_timezone(...)`——旧实现在合法值那
  几个 case 上同样满足，等于没测。跟本 PR 前面"断言带偏移 vs 断言精确 +08:00"是同一个坑。
- `add_user(timezone="")` 之后要**回读确认空串真的落库了**。Pydantic 的
  `Field(default="UTC")` 只在字段缺失时生效，显式传 `""` 会落库——但这条一旦哪天变了，测试
  会静默退化成"测了个 UTC 直通"而不是失败。所以那句回读断言是测试的一部分，不是调试残留。

**没有**给 `users.timezone` 加 DB 约束或迁移来"根治"：那是铁律 #6 的范围，本次定位是应用层收口。

## 2026-08-18 — `get_user_timezone` 的实现追上它的 docstring

原来 docstring 写着「returns 'UTC' if user does not exist」，实现却是用户存在就
`return user.timezone` 原样返回——空串、非法时区串一律透出。**兜底责任因此散在每个调用方
身上**：靠他们各自记得包一层 `resolve_timezone`。到 2026-08-18 有 6 个调用点，其中 3 个没包。

大部分没包的没炸，是因为下游又兜了一次（`step_4` 靠 `scan_reply_for_date_claims` 内部救回、
`basic_info_module` 靠 `format_now_for_agent` 内部救回）。**唯一没有第二道网的是
`services/instance_sync_service.py`**：它把这个值冻进 Job 的 `trigger_config`，于是一个非法
时区串被**持久化**，之后每次算调度都读它，最终在 `ZoneInfo()` 处抛。

所以规则现在收在源头：返回值永远是可用的 IANA 时区。已经包了 `resolve_timezone` 的调用点
不受影响（幂等），没包的自动补齐。`resolve_timezone` 的 docstring 说自己是
"Centralised so that a missing or malformed users.timezone degrades the same way
everywhere" —— 这次才真的 centralised。

**行为变更**：`instance_sync_service` 从此把 `"UTC"` 而不是原始非法值写进 `trigger_config`。
已确认下游安全——job 侧全部是 `(... .timezone if ... else None) or "UTC"` 的形状，空串和
`"UTC"` 对它们等价；而 `_job_scheduling.compute_next_run` 反而要求 `timezone` 非空，写
`"UTC"` 比写空串更合它的意。

新增 import：`utils.timezone.resolve_timezone`。方向是 repository → utils，向下不倒置；
`utils/timezone.py` 只依赖 stdlib + loguru，不成环。

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
