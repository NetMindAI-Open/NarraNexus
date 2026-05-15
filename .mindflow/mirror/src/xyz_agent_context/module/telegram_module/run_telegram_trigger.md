---
code_file: src/xyz_agent_context/module/telegram_module/run_telegram_trigger.py
stub: false
last_verified: 2026-05-09
---

## Why it exists

Standalone process entry point that boots ``TelegramTrigger`` outside
of the FastAPI backend. Each IM channel has one (``run_lark_trigger``,
``run_slack_trigger``, ``run_telegram_trigger``) so triggers can be
launched independently from a tmux pane in dev or a managed sidecar
in production.

Symmetric with the other channels' launchers — same shape, same
shutdown semantics, same auto-migrate guarantee.

## Design decisions

- **Auto-migrate on boot.** Calls ``schema_registry.auto_migrate``
  before instantiating the trigger so a fresh checkout (or a clean
  prod deploy) creates ``channel_telegram_credentials`` /
  ``channel_seen_messages`` / etc. without a separate migration
  step. Idempotent — safe to call repeatedly.
- **Default 3 base workers.** Same default as Slack's launcher;
  trigger's ``base_workers`` plus per-subscriber workers handle
  burst. The base class scales workers automatically as credentials
  arrive.
- **Catches both ``KeyboardInterrupt`` and ``asyncio.CancelledError``
  for clean shutdown.** ``trigger.stop()`` flushes the long-poll
  client (with a 3s timeout per credential — see
  ``TelegramTrigger.stop``) so we don't leak aiohttp sessions.
- **``setup_logging("telegram_trigger")``** routes to a dedicated
  log file so trigger noise doesn't clutter the main backend log.
  Same convention as the Lark / Slack launchers.
- **Top-level ``while True: await asyncio.sleep(1)``.** Trigger
  workers run inside the ``trigger.start`` task graph; the main
  coroutine just needs something to await on so the loop stays
  alive.
- **Imports inside ``main()``.** Lazy import keeps cold-start fast
  for the no-op case (logging-config import only) and dodges
  circular-import paths through ``schema_registry``.
- **``logger.complete()`` flush.** Loguru sinks are async; without
  the flush we can lose the last few log lines on shutdown.

## Upstream / downstream

- **Started by**: ``scripts/dev-local.sh``, ``run.sh``,
  ``scripts/deploy-cloud.sh`` (each adds a Telegram entry alongside
  the Lark/Slack triggers).
- **Calls**: ``TelegramTrigger``, ``schema_registry.auto_migrate``,
  ``utils.db_factory.get_db_client``.

## Gotchas

- ``await trigger.stop()`` is best-effort with a 3s per-credential
  timeout. SIGKILL after that may still leave aiohttp sessions
  half-closed; don't rely on this for prod hard-restart correctness.
- This launcher is one of the four required dev processes (after
  the FastAPI backend, MCP server bundle, ModulePoller); a Telegram
  bot won't receive messages until this is running.
- Removing ``auto_migrate`` here will silently break a fresh clone —
  the trigger queries tables that may not exist yet.
