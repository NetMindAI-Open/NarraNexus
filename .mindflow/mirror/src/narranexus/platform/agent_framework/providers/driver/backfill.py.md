---
code_file: src/narranexus/platform/agent_framework/providers/driver/backfill.py
last_verified: 2026-08-27
stub: false
---

## 2026-08-27 — auth_ref 的外置 driver_type 守卫删除

守卫收进 `derive_auth_ref` 本体(见 derive.py.md 同日条目),这里的
`if driver_type in {claude_oauth, codex_oauth}` 变成第二道重复守卫,
删除。行为不变:非 CLI 订阅行 derive 返回 None,`if auth_ref is not None`
分支照旧跳过。另注:自 2026-08-27 起新行在插入时即写全三字段
(user_service `_cli_subscription_row_fields`),本 backfill 只剩
「pre-driver 老库升级」和「读时推导的持久化」两个职责——不要因为
`ProviderCard.from_row` 有读时兜底就删掉它。

# backfill.py — one-shot startup migration

Fills the four new ``user_providers`` columns (``driver_type`` /
``owner_user_id`` / ``billing_policy`` / ``auth_ref``) on rows
created before Phase 0 shipped. ``backend.main.lifespan`` calls it
once right after ``auto_migrate`` returns. Cheap, idempotent.

## Loop strategy

One row at a time, derive in Python, then ``db.update`` — instead of
a single bulk SQL CASE expression. Reasons:

* Easier to test (table-driven inputs to derive_* functions).
* Easier to change (add a new source / driver_type without touching
  SQL).
* ``user_providers`` never has more than ~hundred rows per tenant.
  The loop is negligible compared to backend boot.

## Skip semantics

A row whose ``(source, auth_type, protocol)`` triple doesn't map to a
known driver_type gets logged as ``[backfill] Cannot classify ...``
and left alone. The resolver will refuse to handle it (loud error)
rather than us guessing wrong. This is intentional — silent fallback
to ``custom_openai`` because a row "looks openai-ish" would be the
exact mis-feature this whole package exists to fix.

## Re-run safety

The loop scans all provider rows but only fills missing metadata, so
already-classified rows are normally no-ops. The one deliberate exception
is OAuth ``auth_ref`` canonicalization: if a Claude OAuth row carries a
Codex sentinel, or a Codex OAuth row carries a Claude sentinel from an
older build, backfill rewrites it to the source-specific canonical value.
That turns stale local data into a healthy row without forcing users to
delete and recreate their provider.

## OAuth auth_ref selection

`derive_auth_ref` receives both `auth_type` and `source`. This matters
because Claude OAuth and Codex OAuth are both `auth_type='oauth'` but
their host CLI credential files live in different places. Backfill must
write `claude-cli:~/.claude/.credentials.json` for Claude rows and
`codex-cli:~/.codex/auth.json` for Codex rows.
