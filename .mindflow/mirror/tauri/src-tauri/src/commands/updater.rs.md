---
code_file: tauri/src-tauri/src/commands/updater.rs
last_verified: 2026-05-27
---

## 2026-05-27 — unified state machine (rewrite, Owner spec)

Previous design had three half-aligned UX paths: Settings page
showed text in the page, tray menu and startup auto-check both
showed osascript dialogs. Symptoms in production:
1. Settings "Check for updates" button spun forever with no
   progress feedback while a 400 MB download ran silently in the
   background (the v1.7.5 incident — user thought it hung).
2. State surfaces never agreed: clicking tray then opening
   Settings would not show "downloading 35%", it would show idle.
3. `updater.check()` had no hard timeout — an unreachable
   endpoint pinned the whole pipeline indefinitely.

Rewrite (Owner spec, 2026-05-27): **single Rust state machine,
three UI entry points all feed it, three UI surfaces all mirror
it via the `updater:state` event**.

State machine:
```
Idle → Checking → UpToDate { current, checked_at }
              ├─→ Available { version, notes? }
              │     └─→ Downloading { downloaded, total?, percent? }
              │           └─→ Installing { version }
              │                 └─→ Ready { version }  → user clicks Restart → app.restart()
              └─→ Failed { stage, error } (from any stage)
```

Key invariants:
- **Auto-install (Q1=A in design)**: once `Available` is reached
  the pipeline downloads + installs without asking. The "ask" is
  the Ready banner at the end. By the time the user sees the
  banner the bytes are already on disk — restart is instant.
- **250 ms progress throttle (Q3)**: without it, ~16 KB-per-chunk
  callbacks on a 400 MB bundle emit ~25 k events and wedge the
  IPC / WS channel.
- **30 s check timeout**: the v1.7.5 incident proved reqwest's
  default infinite connect timeout is too generous. After 30 s
  the pipeline transitions to `Failed{stage:"check"}` and the UI
  stops spinning.
- **Reentrancy guard**: a `tokio::sync::Mutex::try_lock` around
  the pipeline so a second click while one is in flight is a
  no-op, not a queued duplicate run.

### IPC surface

| command | purpose |
|---------|---------|
| `updater_check` | Kick a fresh check → download → install pipeline. Returns immediately; progress arrives via the `updater:state` event. |
| `updater_get_state` | Snapshot the current state. Frontend calls on mount to recover state if a startup-auto pipeline already transitioned before React attached its listener. |
| `updater_restart` | Restart the app. Frontend gates on `state.kind === "ready"`. |

### Internal

- `run_pipeline(app)` — the shared check → download → install
  routine all three entry points call (startup hook, tray click,
  Settings button).
- `run_startup_pipeline(app)` — thin wrapper called from
  `lib.rs::setup` (bundled only). Just forwards to
  `run_pipeline`; kept as a separate symbol so the startup
  call-site has a self-documenting name.
- `set_state(app, next)` — mutates `AppState.updater_state` AND
  emits `updater:state` in one step. Every transition goes
  through here so UI surfaces never see a stale store.

## Upstream / downstream

**Subscribed to `updater:state`** (UI mirrors of the state):
- Rust [[tray]] listens and rewrites the "Check for Updates…"
  menu-item label live (Downloading 35% → Restart to apply 1.7.11).
- Frontend [[updaterStore.ts]] hydrates the Zustand store + drives
  [[UpdateBanner.tsx]] (global top-center pill, shown only on
  `kind:"ready"`) and [[SettingsPage.tsx]]'s `UpdatesSection`
  (full state machine UI with progress bar).

**Calls into**: `tauri_plugin_updater` (`UpdaterExt::updater` →
`check()` → `download_and_install(on_chunk, on_done)`). Plugin is
registered in [[lib]].

## Requires (else `Failed{stage:"check"}` and the app keeps working)

- `tauri.conf.json` `plugins.updater.pubkey` non-empty.
- Build signed with `TAURI_SIGNING_PRIVATE_KEY` + `latest.json`
  + `NarraNexus.app.tar.gz` published at the `endpoints` URL.
- VPN or general network reachability for the
  `https://github.com/NetMindAI-Open/...` endpoint. Without it
  the 30 s timeout fires and the UI reflects
  `Failed{stage:"check"}` instead of spinning forever.

## Gotcha

The installed update applies on the **next launch / restart** —
`download_and_install` replaces the bundle on disk, but the
running process keeps the old code mapped. That is why the Ready
state requires an explicit user-triggered `app.restart()`. We do
not auto-restart even in the silent startup path: the user might
be mid-conversation.
