---
code_file: scripts/check_id_field_coverage.py
last_verified: 2026-05-08
stub: false
---

# check_id_field_coverage.py — ID Rewrite Layer 3 (PRD §8.11)

## 为什么存在

Layer 3 of the 5-layer ID-rewrite defense. Walks every column in
`schema_registry.TABLES` whose name ends in `_id` (or is just `id`) and
asserts each one is either:

1. Registered in `bundle.id_field_map.STRUCTURED_ID_FIELDS` (will be
   actively rewritten on bundle import), OR
2. Listed in this file's `IGNORE` dict with a one-line reason for the
   exemption (surrogate PKs, user_id columns handled by global remap,
   tables that are stripped from the bundle, etc.).

Anything not in either bucket is a **silent rewrite-rule gap** — the
import flow would just skip it without warning, leaving stale ID
references after a roundtrip. This script makes that bug compile-time
visible.

## 上下游

- **Consumes**: `xyz_agent_context.utils.schema_registry.TABLES`,
  `xyz_agent_context.bundle.id_field_map.STRUCTURED_ID_FIELDS`
- **Run by**: developer locally; ideally also wired into CI
  (`make lint` or pre-commit hook)

## How to maintain

When you add a new table or new `*_id` column to `schema_registry`:

1. Run this script. It tells you the missing column.
2. Decide:
   - If the column is a logical reference that must be rewritten on
     bundle import → add an entry under the table in
     `STRUCTURED_ID_FIELDS` with the correct kind (one of the values
     in `id_schema.ID_KINDS`).
   - If the column is exempt (surrogate PK, user_id global remap,
     non-bundled table, external-system id) → add it to `IGNORE`
     here with a one-line reason.

Run again to confirm OK.
