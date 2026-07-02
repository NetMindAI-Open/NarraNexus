---
code_file: src/xyz_agent_context/services/model_sync_runner.py
last_verified: 2026-06-24
stub: false
---

# services/model_sync_runner.py — daily driver for provider model auto-sync

## Why it exists

[[model_sync]] is the engine (catalog fetch + probe + ledger); this is the thing
that *runs* it on the cloud. The repo has no general scheduler, so this is a
small standalone service (same shape as [[module_poller]] / message_bus_trigger):
a daily-at-05:00-UTC loop that refreshes the ledger and overwrites every user's
provider model lists.

## How it works / design

- `run_once()`: for each source with a key in env (`NETMIND_API_KEY` →
  netmind+system_pool, `OPENROUTER_API_KEY`, `YUNWU_API_KEY`), call
  `model_sync.sync_source` (probe new/failed, refresh the ledger), then
  `model_sync.apply_ledger_to_db(db)` — one bulk dialect-safe `db.update` per
  (source, protocol) overwriting **all** users' rows. One source failing is
  logged and skipped; it never aborts the rest.
- `run_loop()`: sleeps to the next 05:00 UTC and repeats; survives any single
  pass crashing.
- Two run modes: `python -m …model_sync_runner` (one pass — used by the release
  `make models-refresh` step + dev) and `… --loop` (the cloud compose service
  `narranexus-model-sync`).

## Gotchas

- The probe result is a backend property, not per-key — so one pass with the
  platform key updates every user. system_pool rows are overwritten from the
  netmind ledger entry.
- Ledger write is best-effort (read-only container rootfs just loses the dedup
  cache; the DB rows are the durable output, next run re-probes).
- Lifecycle is logged (start/per-source/done/error); a dedicated audit table is
  a future nicety, not wired yet.
