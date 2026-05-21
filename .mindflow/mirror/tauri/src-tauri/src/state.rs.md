---
code_file: tauri/src-tauri/src/state.rs
last_verified: 2026-05-18
---

## 2026-05-18 — `pending_deep_link` field

`AppState` gains `pending_deep_link: Arc<StdMutex<Option<String>>>` —
buffer for `narranexus://` URLs the OS hands the process before the
React frontend mounts a `deep-link-received` event listener (events
fired with no listener are dropped). The deep-link `on_open_url`
callback in `lib.rs::setup` writes here; the frontend drains it via the
`consume_pending_deep_link` Tauri command on first mount. See
`.mindflow/mirror/tauri/src-tauri/src/lib.rs.md` and
`drafts/logs/template_sharing_2026_05_18.md`.

# state.rs — AppState, ServiceDef, and path resolution for the Tauri app

The central configuration file for the desktop app. Defines:

- `AppConfig` — mode (local/cloud), api_base_url, python_path, db_path
- `ServiceDef` — one per child process (id, label, command, args, cwd, port,
  health_url, order, startup_delay_ms)
- `AppState` — Tauri managed state holding config, process_manager,
  health_monitor, and service_defs
- Path resolution helpers: `resolve_resource_dir`, `resolve_project_root`,
  `resolve_python_path`, `resolve_db_path`, `is_bundled`

## Why path resolution is non-trivial

A macOS `.app` bundle has a specific directory layout:
```
NarraNexus.app/
  Contents/
    MacOS/narranexus        ← executable
    Resources/resources/
      project/              ← Python project root
      python/bin/python3    ← bundled Python
```

Development layout:
```
tauri/src-tauri/     ← CWD during dev
../../              ← project root (two levels up)
uv                  ← Python via PATH
```

`is_bundled()` detects which layout is active by checking for the bundled
Python path. The service factories (`bundled_services` vs `dev_services`)
choose the right commands based on this.

## The two service factories

`bundled_services` uses the absolute Python path directly (no `uv`).
`dev_services` prefixes all commands with `uv run python ...` for the
virtual-env-managed dev workflow.

Both factories define the same nine services in the same order:
1. sqlite_proxy (order 0, 3 s startup delay)
2. backend (order 1) — uvicorn args include `--ws-ping-interval 30
   --ws-ping-timeout 60` so long-running Agent turns don't drop the WS stream
3. mcp (order 2)
4. poller (order 3)
5. job_trigger (order 4)
6. message_bus_trigger (order 5)
7. lark_trigger (order 6)
8. slack_trigger (order 7) — Socket Mode subscriber, one WS per
   channel_slack_credentials row. No bound port (Socket Mode is outbound).
9. telegram_trigger (order 8) — long-poll subscriber, one async loop per
   channel_telegram_credentials row. No bound port (long-poll is outbound).

**These MUST stay in sync with `scripts/dev-local.sh` AND with
`NarraNexus-deploy/stacks/narranexus-app/compose.yml` (CLAUDE.md rule #7).**
Specifically the backend uvicorn ws-ping args above — mismatching the two
makes chat streams drop on the dmg while they survive on dev. Slack/Telegram
triggers were originally only wired into `dev-local.sh`, causing them to
silently miss on both dmg and EC2 — that gap is now closed; adding any new
trigger MUST touch all three startup paths together.

## The SQLite proxy startup delay

Order 0 (sqlite_proxy) has `startup_delay_ms: Some(3000)`. This mirrors the
`sleep 3` in `scripts/dev-local.sh`. Without this delay, backend/mcp/poller
try to connect to the proxy before it binds port 8100 and crash. The value
was chosen empirically — on slow machines 3 s may not be enough.

## Bundled Node.js + CLI helper

`resolve_bundled_node_bins()` returns the two directories that must be on
PATH for every Python child process when running from the dmg:
  `resources/nodejs/bin` (real node interpreter) and
  `resources/nodejs/node_modules/.bin` (the `claude` / `lark-cli` shims).

Layout is produced by `scripts/build-desktop.sh` step 3.5-3.6. In dev mode
the helper returns an empty Vec — dev users already have node + CLIs on
their shell PATH.

`sidecar::process_manager::start_service` reads this helper and prepends the
paths to the PATH env var it passes each child. Without that prefix,
`claude_agent_sdk` cannot find the `claude` binary and the chat loop dies
with "No such file or directory".

## Gotchas

`resolve_db_path` always uses `~/.narranexus/nexus.db` regardless of mode.
There is no per-user or per-environment isolation. Running two agents
simultaneously from different installations shares the same database.
