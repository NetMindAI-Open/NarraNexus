---
code_file: tauri/src-tauri/src/state.rs
last_verified: 2026-07-22
---

## 2026-07-22 — four worker ServiceDefs collapsed into one `workers` service

Both factories replaced the four separate worker ServiceDefs (`poller` o3,
`job_trigger` o4, `message_bus_trigger` o5, `channel_triggers` o6) with a SINGLE
`workers` service (id `workers`, label "Workers", order 3) running
`python -m xyz_agent_context.module.run_worker_supervisor` — one process running
the module poller, job / message-bus triggers, and every IM channel trigger in
one event loop (see [[run_worker_supervisor.py]]). So each factory now defines
**FOUR** services (orders 0–3): sqlite_proxy, backend, mcp, workers — down from
seven. `is_required_service` in [[process_manager.rs]] was updated to
`sqlite_proxy | backend | mcp | workers` (an unconfigured channel never fails
`workers` — per-channel startup is isolated in `start_channel_triggers`). The
"seven services (orders 0–6)" section below is now HISTORY. MCP remains its own
service. Startup-path alignment is guarded by
`tests/channel/test_trigger_startup_alignment.py`.

## 2026-07-13 — pending_netmind_oauth buffer

Added `pending_netmind_oauth: Arc<StdMutex<Option<String>>>` — the buffered
desktop NetMind OAuth result, drained by `take_netmind_oauth_result`. Same
pattern/rationale as `pending_deep_link`: poll-based delivery so it never depends
on a live Tauri event listener. Written by [[netmind_oauth.rs]]'s on_navigation.

## 2026-07-08 — six channel triggers consolidated into one `channel_triggers` service

Both factories replaced the six per-channel `ServiceDef`s (lark / slack /
telegram / discord / wechat / narramessenger, orders 6–11) with a SINGLE
`channel_triggers` service (order 6) running
`xyz_agent_context.module.run_channel_triggers` — one supervisor process that
drives every channel in one event loop. See
[[run_channel_triggers.py]] / [[channel_trigger_map.py]]. So each factory now
defines SEVEN services (orders 0–6), not twelve. The alignment guard test was
rewritten accordingly: it no longer checks per-`run_*_trigger` wiring but that
the single `run_channel_triggers` entrypoint appears in `run.sh` /
`dev-local.sh` / `.dev-local-safe.sh` / `deploy-cloud.sh` / both factories here,
plus registry completeness (`REGISTERED_TRIGGER_CLASS_NAMES`). The dated
per-channel entries below are retained as history but describe the pre-2026-07-08
layout.

## 2026-07-02 — `wechat_trigger` added (order 11) + alignment guard test

Both factories gain `wechat_trigger` (order 11, after discord) — iLink
long-poll subscriber, one loop per `channel_wechat_credentials` row, no
bound port. The WeChat module shipped 2026-06-24 wired into `run.sh` +
`dev-local.sh` only, so every dmg through v1.8.4 exposes the WeChat bind
UI but never polls — the exact Slack/Telegram gap class documented below.
That class is now enforced by
`tests/channel/test_trigger_startup_alignment.py`: every
`module/*_module/run_*_trigger.py` must appear in `run.sh`,
`dev-local.sh`, and BOTH factories here (cloud compose is guarded by a
check script in the deploy repo).

## 2026-06-18 — `narramessenger_trigger` added (order 9)

Both factories gain a tenth service, `narramessenger_trigger` (order 9) —
gateway long-poll subscriber, one async loop per
`channel_narramessenger_credentials` row, no bound port. Added in lockstep
across all startup paths per rule #7: `scripts/dev-local.sh`, `run.sh`, and
both factories here. **Still pending: `NarraNexus-deploy/.../compose.yml`**
(separate deploy repo) — must add the trigger there before the cloud deploy
or inbound NarraMessenger silently misses on EC2 (same class of gap that bit
Slack/Telegram).

## 2026-05-27 — `updater_state` field (unified auto-updater)

`AppState` gains `updater_state: Arc<StdMutex<UpdaterState>>` —
the single source of truth for the unified updater state machine
(see [[updater.rs]] for the enum + pipeline). All three entry
points (startup auto-check in lib.rs, tray "Check for Updates…"
in tray.rs, Settings page button via `updater_check` IPC) mutate
this same field; the UI surfaces (global banner / Settings panel
via [[updaterStore.ts]] / tray menu label via the listener in
[[tray.rs]]) all mirror it via the `updater:state` Tauri event.

`std::sync::Mutex` (not tokio) because state writes are quick
and never span await boundaries — every `set_state` in
updater.rs locks → mutates → emits → drops the guard.
Initialised at `UpdaterState::Idle` in `AppState::default`.

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

Both factories define the same seven services in the same order (since the
2026-07-08 consolidation — see the top entry):
1. sqlite_proxy (order 0, 3 s startup delay)
2. backend (order 1) — uvicorn args include `--ws-ping-interval 30
   --ws-ping-timeout 60` so long-running Agent turns don't drop the WS stream
3. mcp (order 2)
4. poller (order 3)
5. job_trigger (order 4)
6. message_bus_trigger (order 5)
7. channel_triggers (order 6) — ONE supervisor process
   (`xyz_agent_context.module.run_channel_triggers`) running every IM channel
   (Lark / Slack / Telegram / Discord / WeChat / NarraMessenger) in a single
   event loop. Replaced the six per-channel services. No bound port.

**These MUST stay in sync with `scripts/dev-local.sh` AND with
`NarraNexus-deploy/stacks/narranexus-app/compose.yml` (CLAUDE.md rule #7).**
Specifically the backend uvicorn ws-ping args above — mismatching the two
makes chat streams drop on the dmg while they survive on dev. Adding a new
channel now means registering its trigger class (no new `ServiceDef`); adding
any non-channel service MUST still touch all startup paths together.

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
