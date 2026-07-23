---
code_file: src/xyz_agent_context/services/skill_sync_service.py
last_verified: 2026-07-22
stub: false
---

# services/skill_sync_service.py — skill 对账器

Keeps `skill_installations` (audit DB) following the filesystem truth.
Users can always hand-edit `skills/` — so the DB is a FOLLOWER: this
service ONLY writes DB rows, never touches user files (tested explicitly:
a reconcile pass leaves the tree byte-identical).

## Drift matrix

disk-only → row added (source from `.skill_meta.json`, else `manual`);
DB-installed but gone from disk → `external_removed` (row kept for audit);
content hash ≠ recorded `content_hash` → `modified`; under `.disabled/` →
`disabled`; back on disk → `installed` again. Idempotent: pass 2 is a
no-op. `uninstalled`/`external_removed` rows are terminal-stable (not
re-flagged every pass).

## Wiring

Runs inside the backend lifespan (`backend/main.py`) as a single
fire-and-forget `create_task(run_forever())` — the lifespan does NOT run a
separate blocking startup pass and does NOT await the first reconcile
(scanning every workspace + hashing every installed skill grows with users;
blocking startup on it was the 2026-07-22 lifespan-latency fix). Instead
`run_forever` does its FIRST reconcile immediately inside the loop, then
sleeps `SKILL_SYNC_INTERVAL_SECONDS` (default 1800) between passes. Setting
the interval `<= 0` returns before the loop even starts, so it disables the
ENTIRE reconciler — no first pass and no periodic loop (tests opt out this
way). Cancelled on shutdown with a done-callback that logs unexpected exits
(fire-and-forget lesson #2). Lives in the backend process — both run modes
(run.sh / DMG sidecar) boot the same lifespan, iron rule #7.
`reconcile_all` walks the NESTED workspace layout `{user_id}/{agent_id}`
(see utils/workspace_paths.py `_LAYOUT`); if the layout flips again this
walk must follow.

Enable/disable via the UI routes does NOT write the audit table directly —
the reconciler is what converges those states (acceptable lag ≤ interval;
DB is audit-only).
