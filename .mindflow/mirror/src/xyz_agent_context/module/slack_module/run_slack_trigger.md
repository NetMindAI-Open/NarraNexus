---
code_file: src/xyz_agent_context/module/slack_module/run_slack_trigger.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Standalone entry point so the Slack trigger can run as its own process
(systemd unit, Docker container, ``uv run python -m ...``). Triggers
are infrastructure — they need to outlive any single MCP server or
backend restart, so they get their own supervisor-friendly main.

Mirror of ``run_lark_trigger.py``.

## Design decisions

- **Imports inside ``main``, not module top.** Defers
  ``xyz_agent_context.module.slack_module.slack_trigger`` import until
  after logging is configured. Avoids the trigger pulling in
  ``slack_sdk`` (and possibly logging warnings) before our log
  format is set up.
- **``auto_migrate`` runs on every startup.** Idempotent at the
  schema-registry layer; ensures
  ``channel_slack_credentials`` and the shared ``channel_*`` tables
  exist before the trigger tries to read from them. No separate
  bootstrap script.
- **Loops on ``asyncio.sleep(1)`` after start.** The trigger's lifecycle
  (workers, credential watcher, sockets) runs on tasks the base class
  manages — main just stays alive so those tasks stay alive.
  ``KeyboardInterrupt`` and ``CancelledError`` route through ``stop()``.
- **``logger.complete()`` drained before ``asyncio.run`` tears down.**
  Loguru's ``enqueue=True`` async sinks must be flushed inside the
  same loop; doing it after ``asyncio.run()`` returns drops messages.
  Same gotcha and fix as ``run_lark_trigger.py``.

## Upstream / downstream

- **Upstream**: process supervisor (Makefile target, systemd, Docker
  ENTRYPOINT) invokes this module.
- **Downstream**:
  - ``SlackTrigger`` (the actual workhorse).
  - ``get_db_client`` (singleton DB).
  - ``auto_migrate`` (schema registry idempotent migration).
  - ``setup_logging("slack_trigger")`` — names this process in shared
    logs so multi-trigger deployments can grep cleanly.

## Gotchas

- Run with ``uv run python -m
  xyz_agent_context.module.slack_module.run_slack_trigger`` from the
  repo root, NOT direct ``python file.py`` (the absolute imports break).
- ``max_workers=3`` is the **base** worker count; the trigger scales
  up to ``MAX_WORKERS=50`` per ``slack_trigger.py``'s own constants.
  Don't tune it here without understanding the worker-per-subscriber
  multiplier on the base class.
