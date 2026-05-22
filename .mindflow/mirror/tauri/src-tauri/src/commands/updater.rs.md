---
code_file: tauri/src-tauri/src/commands/updater.rs
last_verified: 2026-05-22
---

# updater.rs — app auto-update (check / download / install)

## Why it exists / why Rust

Drives `tauri_plugin_updater` (already registered in `lib.rs`) to check the
release endpoint, download + install a newer **signed** build, and offer a
restart. Implemented in Rust on purpose: the frontend deliberately ships NO
`@tauri-apps/*` npm deps (web/cloud build stays clean), so doing it in JS would
mean adding those deps or hand-rolling the updater plugin's `Update` resource
protocol over `invoke`. Rust uses `UpdaterExt` directly.

## Surface
- `check_and_install_update(app) -> Result<String,String>` — the `#[tauri::command]`
  behind the Settings "Check for updates" button (frontend invokes it via
  `lib/tauri.ts::checkForUpdates`). Returns `"up_to_date"` / `"installed:<v>"`.
- `run_startup_update_check(app)` — spawned on startup by `lib.rs` (bundled
  builds only); silently checks + installs, then `prompt_restart` (osascript,
  same mechanism as `port_preflight`) offers `app.restart()`.

## Requires (else the check just errors + logs, never blocks the app)
- `tauri.conf.json` `plugins.updater.pubkey` non-empty (the updater public key).
- The build signed with `TAURI_SIGNING_PRIVATE_KEY` and `latest.json` +
  `NarraNexus.app.tar.gz` published at the `endpoints` URL — see
  `build-desktop.yml` "Build + sign updater artifact". The private key lives in
  GitHub Secrets, never in the repo.

## Gotcha
The installed update applies on the **next launch / restart** (download_and_install
replaces the bundle; the running process keeps the old code). That's why the
startup path offers a restart.
