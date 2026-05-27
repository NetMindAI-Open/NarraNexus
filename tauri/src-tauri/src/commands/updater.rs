//! @file_name: updater.rs
//! @description: App auto-update — check the GitHub release endpoint, download
//! + install a newer signed build, and (on startup) offer to restart.
//!
//! Done in Rust (not the frontend) on purpose: the frontend deliberately ships
//! NO `@tauri-apps/*` npm deps (web/cloud build stays clean), so driving the
//! updater plugin's `Update` resource from JS would mean either adding those
//! deps or hand-rolling the resource protocol over `invoke`. Rust uses the
//! already-registered `tauri_plugin_updater` directly.
//!
//! Requires (see tauri.conf.json `plugins.updater`): a non-empty `pubkey` and
//! the build signed with `TAURI_SIGNING_PRIVATE_KEY`, plus `latest.json` +
//! the signed `.app.tar.gz` published at the `endpoints` URL. Without those the
//! check simply errors and is logged — it never blocks the app.

use std::process::Command;

use tauri::AppHandle;
use tauri_plugin_updater::UpdaterExt;

/// Check for an update and, if one exists, download + install it. Returns a
/// short status string for the frontend "Check for updates" button:
///   "up_to_date" | "installed:<version>" ; Err(msg) on failure.
/// The installed update applies on the next app launch (or an explicit
/// restart). Safe to call from a Settings button or on startup.
#[tauri::command]
pub async fn check_and_install_update(app: AppHandle) -> Result<String, String> {
    let updater = app
        .updater()
        .map_err(|e| format!("updater not available: {e}"))?;
    match updater.check().await {
        Ok(Some(update)) => {
            let version = update.version.clone();
            update
                .download_and_install(|_chunk, _total| {}, || {})
                .await
                .map_err(|e| format!("download/install failed: {e}"))?;
            log::info!("[updater] installed update {version} (applies on restart)");
            Ok(format!("installed:{version}"))
        }
        Ok(None) => {
            log::info!("[updater] already up to date");
            Ok("up_to_date".to_string())
        }
        // Return the underlying reqwest / updater error as-is — callers
        // wrap with their own context ("Update check failed.\n\n{e}" in
        // the manual flow, log "[updater] startup check: {e}" silently
        // in the startup flow). Adding "update check failed:" here too
        // produced the duplicated "Update check failed.\n\nupdate check
        // failed: <real error>" dialog seen in v1.7.7.
        Err(e) => Err(e.to_string()),
    }
}

/// Startup auto-check: silently check + install, then (if something was
/// installed) offer a restart via a native dialog. Best-effort — any failure
/// is logged and never blocks the app. Caller gates this to bundled/release
/// builds (dev has no real release to update from).
///
/// "Up to date" and "check failed" branches are intentionally silent on
/// startup — the user didn't ask for this check, so flashing a dialog
/// every launch would be annoying. The tray menu's "Check for Updates…"
/// entry point uses `run_manual_update_check` which always shows
/// feedback.
pub async fn run_startup_update_check(app: AppHandle) {
    match check_and_install_update(app.clone()).await {
        Ok(status) if status.starts_with("installed:") => {
            let version = status.trim_start_matches("installed:");
            if prompt_restart(version) {
                log::info!("[updater] user accepted restart to apply {version}");
                app.restart();
            } else {
                log::info!("[updater] update {version} will apply on next launch");
            }
        }
        Ok(_) => {}
        Err(e) => log::warn!("[updater] startup check: {e}"),
    }
}

/// Manual "Check for Updates…" — triggered from the tray menu. ALWAYS
/// shows a native dialog so the user knows what happened. On startup the
/// silent path is fine (they didn't ask), but for an explicit user click
/// silence is confusing — they don't know whether the click did nothing
/// or whether the check failed.
pub async fn run_manual_update_check(app: AppHandle) {
    match check_and_install_update(app.clone()).await {
        Ok(status) if status.starts_with("installed:") => {
            let version = status.trim_start_matches("installed:");
            if prompt_restart(version) {
                log::info!("[updater] user accepted restart to apply {version}");
                app.restart();
            } else {
                log::info!("[updater] update {version} will apply on next launch");
                show_info_dialog(
                    "NarraNexus Update",
                    &format!(
                        "Update {version} was installed. It will apply on the next launch."
                    ),
                );
            }
        }
        Ok(_) => {
            show_info_dialog(
                "NarraNexus Update",
                "You are already on the latest version.",
            );
        }
        Err(e) => {
            log::warn!("[updater] manual check failed: {e}");
            show_info_dialog(
                "NarraNexus Update",
                &format!("Update check failed.\\n\\n{e}"),
            );
        }
    }
}

/// Native restart prompt (osascript — same mechanism as the port-conflict
/// dialog; no Tauri window required). Returns true if the user chose to
/// restart now.
fn prompt_restart(version: &str) -> bool {
    let msg = format!(
        "NarraNexus downloaded version {}.\\n\\nRestart now to apply the update?",
        version
    );
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus Update" buttons {{"Later", "Restart Now"}} default button "Restart Now""#,
        msg
    );
    match Command::new("osascript").args(["-e", &script]).output() {
        Ok(out) => String::from_utf8_lossy(&out.stdout).contains("Restart Now"),
        Err(_) => false,
    }
}

/// Show a one-button OK dialog. Used by `run_manual_update_check` so the
/// tray-click flow always surfaces a result.
fn show_info_dialog(title: &str, body: &str) {
    let script = format!(
        r#"display dialog "{}" with title "{}" buttons {{"OK"}} default button "OK""#,
        body, title
    );
    let _ = Command::new("osascript").args(["-e", &script]).output();
}
