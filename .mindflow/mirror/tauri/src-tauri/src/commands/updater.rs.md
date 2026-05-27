---
code_file: tauri/src-tauri/src/commands/updater.rs
last_verified: 2026-05-27
---

## 2026-05-27 — `run_manual_update_check` + de-Chinese dialogs

New entry point `run_manual_update_check(app)` — called from the tray
"Check for Updates…" item ([[tray]]). Wraps `check_and_install_update`
and ALWAYS surfaces a native dialog with the result:
- installed → restart prompt (if user declines, show "applies on next
  launch" info dialog)
- up-to-date → "You are already on the latest version" info dialog
- failed → "Update check failed.\\n\\n{e}" info dialog

Why a separate function instead of widening `run_startup_update_check`:
the startup path is silent on the up-to-date / failed branches on
purpose (user didn't ask, so flashing a dialog every launch is
annoying). Manual click is the opposite — silence is confusing.

Also de-Chinesed the existing `prompt_restart` strings (违反铁律 #1 的
pre-existing issue, fixed in the same commit since the file was already
being touched). New `show_info_dialog(title, body)` helper factors out
the osascript boilerplate so both flows share one dialog primitive.

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
