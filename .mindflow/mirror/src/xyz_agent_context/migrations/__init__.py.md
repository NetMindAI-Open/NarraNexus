---
code_file: src/xyz_agent_context/migrations/__init__.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 注册 m0003

REGISTRY 追加 `_m0003`(云端 codex_cli→claude_code,顺序在 m0002 之后)。见
[[m0003_cloud_codex_to_claude]]。

# migrations/__init__.py — versioned data-migration ledger + runner

## Why it exists

The unified-memory work needed to backfill EXISTING databases (a long-lived
cloud DB, a `bash run.sh` SQLite, a DMG desktop SQLite all carry pre-refactor
data). There was no version-tracked data-migration mechanism — only
`auto_migrate` (idempotent DDL) and `one_shot_migrations.py` (narrow always-run
self-heals). This package adds the missing layer: **ordered, run-once, versioned
DATA migrations**, the "layer-by-layer upgrade" ledger.

## Design decisions

- **Startup is the only universal hook.** Cloud, `run.sh`, and the DMG sidecar
  all boot the same `backend.main` lifespan. DMG / run.sh users have no CI, so a
  migration MUST run at startup, not in a deploy step. `run_pending_migrations`
  is called from the lifespan after `auto_migrate`.
- **Ledger = `schema_migrations` table** (one row per applied migration id +
  app_version + stats notes). `_applied_ids` reads it; a migration runs only if
  its id is absent, and the row is written ONLY on success.
- **Layer-by-layer cross-version.** A DB last touched on an old version simply
  runs every still-pending migration in REGISTRY order — each authored against
  only its predecessor, so a v1.7→v2.1 jump replays 1.8/1.9/2.0/2.1's steps in
  sequence without any single migration knowing about the jump.
- **Best-effort, non-blocking** (Owner decision 2026-06-09): a migration that
  raises is logged, NOT recorded (retries next startup), and STOPS the chain
  (later migrations may depend on it). It never re-raises — startup is never
  blocked; the caller in main.py also wraps defensively. Search degrades
  gracefully meanwhile.
- **APPEND-ONLY registry.** Never reorder/renumber/mutate a shipped migration —
  its id lives in users' ledgers. Fix a bad migration by adding a new one. Every
  `apply` MUST be idempotent (deterministic record_ids → upsert), because a
  failed run is retried and the ledger row is success-gated.

## Upstream / Downstream

`backend.main:lifespan` → `run_pending_migrations(db)` (after `auto_migrate`).
Each migration lives in its own `mNNNN_<topic>.py` module exporting a single
`MIGRATION` and is listed in REGISTRY. `schema_migrations` is registered in
[[schema_registry]].

## Distinct from neighbours

- `schema_registry.auto_migrate`: idempotent DDL (incl. creating
  `schema_migrations` itself).
- `one_shot_migrations.py`: narrow always-run self-heals (job-timezone,
  singleton-ownership). Kept as-is.
- This package: heavy, one-time, versioned DATA migrations.

## Gotchas

- The ledger table must exist before the runner records — guaranteed by running
  after `auto_migrate`. `_applied_ids` still degrades to "none applied" if the
  read fails (idempotent migrations make that safe).
