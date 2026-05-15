---
code_file: src/xyz_agent_context/module/lark_module/run_lark_trigger.py
stub: false
last_verified: 2026-05-06
---

## Why it exists

Standalone entry point for running `LarkTrigger` as an independent
process.  Used when the trigger needs to run outside the main backend
(e.g., `uv run python -m xyz_agent_context.module.lark_module.run_lark_trigger`).

## Design decisions

- **Calls `auto_migrate`** — ensures all tables (including
  `lark_credentials`) exist before starting the trigger.
- **Runs until KeyboardInterrupt** — simple `while True: sleep(1)`
  loop with graceful `trigger.stop()` on exit.
- **`logger.complete()` is awaited inside `main()`'s `finally`, not in
  a second `asyncio.run()`** — the previous "drain after asyncio.run"
  shape used to log `ValueError: a coroutine was expected` on every
  shutdown, because loguru's async sink had been bound to the original
  loop that `asyncio.run` already closed. Sharing the loop with main
  is the only correct way to flush enqueue=True records before exit.

## Upstream / downstream

- **Upstream**: shell / process manager.
- **Downstream**: `LarkTrigger`, `auto_migrate`, `get_db_client`.
