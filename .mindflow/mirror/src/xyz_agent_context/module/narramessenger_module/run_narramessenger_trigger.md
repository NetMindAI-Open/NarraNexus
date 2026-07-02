---
code_file: src/xyz_agent_context/module/narramessenger_module/run_narramessenger_trigger.py
stub: false
last_verified: 2026-06-18
---

## Why it exists

Standalone process entry point for `NarramessengerTrigger`, mirroring
`run_telegram_trigger.py`. Runs as its own process (one of the long-running
trigger services), gets a DB client, ensures tables via `auto_migrate`, starts
the trigger, and idles until interrupted.

## Design decisions

- Mirrors the telegram entry exactly: `get_db_client()` → `auto_migrate(db._backend)`
  → `NarramessengerTrigger(max_workers=3).start(db)` → sleep loop → `stop()`.
- `setup_logging("narramessenger_trigger")` for a dedicated log stream.

## Run

`uv run python -m xyz_agent_context.module.narramessenger_module.run_narramessenger_trigger`

## Gotchas

- Not yet wired into `run.sh` / the Makefile dev targets — start it manually
  for now (parallels how the other channel triggers are launched as separate
  processes). Add it alongside the other `run_*_trigger` entries when promoting
  past the test phase.
