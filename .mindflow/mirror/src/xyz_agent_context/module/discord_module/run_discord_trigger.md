---
code_file: src/xyz_agent_context/module/discord_module/run_discord_trigger.py
stub: false
last_verified: 2026-06-16
---

## Why it exists

Standalone process entry point for ``DiscordTrigger`` — the long-running
Gateway listener. Launched as its own process by the dev tmux scripts,
the cloud systemd units, and the Tauri sidecar. Mirror of
``run_slack_trigger.py`` / ``run_telegram_trigger.py``.

## Design decisions

- **``auto_migrate`` on startup** so ``channel_discord_credentials`` (and
  the shared channel tables) exist before the watcher polls.
- **Loguru drain in ``finally``** before the loop tears down — same fix as
  the other ``run_*_trigger`` entry points (async ``enqueue=True`` sinks
  must flush or the last log lines are lost on exit).

## Upstream / downstream

- **Upstream**: ``DiscordTrigger``, ``get_db_client``, ``auto_migrate``.
- **Invoked by**: ``scripts/dev-local.sh`` / ``.dev-local-safe.sh`` tmux
  windows, ``scripts/deploy-cloud.sh`` systemd unit, and
  ``tauri/src-tauri/src/state.rs`` sidecar ServiceDef (both prod + dev).

## Gotchas

- Runs with no MCP port of its own — the Gateway WebSocket is outbound.
  (DiscordModule's MCP server on 7834 is a separate process launched by
  ``module_runner``.) The trigger and the MCP server are distinct.
