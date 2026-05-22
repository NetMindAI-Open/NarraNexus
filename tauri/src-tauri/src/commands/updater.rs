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
        Err(e) => Err(format!("update check failed: {e}")),
    }
}

/// Startup auto-check: silently check + install, then (if something was
/// installed) offer a restart via a native dialog. Best-effort — any failure
/// is logged and never blocks the app. Caller gates this to bundled/release
/// builds (dev has no real release to update from).
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

/// Native restart prompt (osascript — same mechanism as the port-conflict
/// dialog; no Tauri window required). Returns true if the user chose to
/// restart now.
fn prompt_restart(version: &str) -> bool {
    let msg = format!(
        "NarraNexus 已下载新版本 {}。\\n\\n重启后生效。现在重启吗？",
        version
    );
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus 更新" buttons {{"稍后", "现在重启"}} default button "现在重启""#,
        msg
    );
    match Command::new("osascript").args(["-e", &script]).output() {
        Ok(out) => String::from_utf8_lossy(&out.stdout).contains("现在重启"),
        Err(_) => false,
    }
}
