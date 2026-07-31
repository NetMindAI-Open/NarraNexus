---
code_file: backend/routes/migrate.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

The HTTP surface of Agent Migration. `/detect` + `/scan` are the read side
(detect + extract other-framework configs into standardized JSON); `/apply` is
the write side (map → `apply_plan` → a populated NarraNexus agent).

## Gotchas

- **`/detect` + `/scan` are local-only.** `_require_local_or_raise()` returns 503
  `migration_local_only` on cloud, because cloud's executor/backend is remote —
  there is no user filesystem to scan. Those two endpoints are desktop/local by
  nature.
- **`/apply` is local-gated too** (2026-07-30) — Agent Migration is a
  desktop/local feature (Owner: cloud disables the whole thing). detect/scan
  already 503 on cloud so there's no legitimate cloud path to `import_data`;
  gating apply closes the direct-POST hole. It builds the plan (`build_plan`)
  and executes it (`apply_plan`), `user_id` from `resolve_current_user_id`.
- `scan` maps `FileNotFoundError` (no framework detected + no path) → 404, other
  extraction errors → 400. Extraction itself never raises (best-effort), so 400
  is rare.
- `/scan` returns `StandardizedAgentImport.model_dump()` verbatim so the caller
  (or user) can edit before POSTing it back to `/apply`.
- **Sync work runs off the event loop** (2026-07-30): `scanner.detect` /
  `scanner.scan` are synchronous and heavy — scan parses session `.jsonl` files
  that can be 100MB+, and `/detect` fires on every local app load. Running that
  directly in an `async def` handler would stall the shared event loop for
  seconds (铁律 #15 — the platform must not become the interruption source), so
  both go through `asyncio.to_thread`. The blocking `shutil` copy in
  `applier._copy_local_skill` is likewise `to_thread`'d.
