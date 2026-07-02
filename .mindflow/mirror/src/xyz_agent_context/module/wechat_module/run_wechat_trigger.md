---
code_file: src/xyz_agent_context/module/wechat_module/run_wechat_trigger.py
stub: false
last_verified: 2026-06-24
---

## Why it exists

Standalone process entry point that boots ``WeChatTrigger`` outside
of the FastAPI backend — the WeChat (iLink) sibling of
``run_telegram_trigger`` / ``run_lark_trigger`` / ``run_slack_trigger``.
Each IM channel runs its trigger as its own process so they can be
launched independently from a tmux pane in dev or a managed sidecar
in production.

Same shape, same shutdown semantics, same auto-migrate guarantee as
the other channels' launchers. The only channel-specific difference
is what the trigger does once started — WeChat PULL-polls the iLink
gateway (``host https://ilinkai.weixin.qq.com``) per bound credential
rather than holding a WS, but the launcher is symmetric.

## Design decisions

- **Auto-migrate on boot.** Calls ``auto_migrate(db._backend)`` before
  instantiating the trigger so a fresh checkout (or a clean prod
  deploy) creates ``channel_wechat_credentials`` /
  ``channel_seen_messages`` / etc. without a separate migration step.
  Idempotent — safe to call on every process start.
- **Default 3 base workers** (``WeChatTrigger(max_workers=3)``). Same
  default as the other channels' launchers; the base class scales
  per-subscriber workers as credentials arrive.
- **Catches both ``KeyboardInterrupt`` and ``asyncio.CancelledError``
  for clean shutdown.** ``trigger.stop()`` flushes the long-poll
  clients so we don't leak aiohttp sessions to the gateway.
- **``setup_logging("wechat_trigger")``** routes to a dedicated log
  file so trigger noise doesn't clutter the main backend log — same
  convention as the Lark / Slack / Telegram launchers.
- **Top-level ``while True: await asyncio.sleep(1)``.** Trigger
  workers run inside the ``trigger.start`` task graph; the main
  coroutine just needs something to await on so the loop stays alive.
- **Imports inside ``main()``.** Lazy import keeps cold-start fast and
  dodges circular-import paths through ``schema_registry`` /
  ``db_factory``.
- **``logger.complete()`` flush** guarded by an ``__await__`` check.
  Loguru sinks are async; without the flush we can lose the last few
  log lines on shutdown.

## Upstream / downstream

- **Started by**: ``run.sh`` and ``scripts/dev-local.sh`` — one
  process per the two run modes (binding rule #7: the two run modes
  must behave identically, so both launch this trigger). Launched as
  ``python -m xyz_agent_context.module.wechat_module.run_wechat_trigger``.
- **Calls**: ``WeChatTrigger``, ``schema_registry.auto_migrate``,
  ``utils.db_factory.get_db_client``.

## Gotchas

- This launcher is a required dev/prod process; a bound WeChat account
  won't receive messages until it's running. Symmetric with the other
  channels — easy to forget when adding WeChat to a new environment.
- Removing ``auto_migrate`` here will silently break a fresh clone —
  the trigger queries ``channel_wechat_credentials`` which may not
  exist yet.
- iLink is PULL-only long-poll, not a webhook; the trigger pacing
  lives inside ``WeChatTrigger``, not here. This file only owns
  boot + graceful shutdown.
