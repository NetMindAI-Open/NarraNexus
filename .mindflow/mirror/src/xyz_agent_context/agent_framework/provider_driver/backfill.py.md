---
code_file: src/xyz_agent_context/agent_framework/provider_driver/backfill.py
last_verified: 2026-05-13
stub: false
---

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

Filter is ``driver_type IS NULL``. Once a row is classified, the next
backfill run skips it. Manual admin edits also stick — we never
overwrite a non-null value.
