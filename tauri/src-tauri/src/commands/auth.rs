//! Claude Code CLI authentication commands.
//!
//! Exposes `trigger_claude_login` / `trigger_claude_logout` so the desktop
//! app can drive the OAuth flow without requiring the user to open a
//! terminal. Each command spawns the bundled `claude` CLI with the
//! Node.js + CLI shim paths prepended to PATH (same injection that
//! `process_manager.rs` does for Python services).

use tauri::State;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};

use crate::state::{resolve_bundled_node_bins, AppState};

/// Detached drainer for a piped stdout/stderr stream from a spawned CLI.
///
/// Why this matters: `tokio::process::Command::stdout(piped())` makes the
/// kernel allocate a ~16 KB buffer; if nobody reads it, the child blocks
/// on its next write the moment the buffer fills. That's the exact same
/// failure mode that hung Python sidecars before `process_manager.rs`
/// added its log drainer. `claude auth login` is currently quiet (a few
/// hundred bytes over the whole OAuth flow) so this isn't load-bearing
/// today — but adding the drainer is what makes us *structurally* immune
/// to a future Anthropic CLI release that decides to be more verbose.
///
/// Each line goes to the standard `log::info!` macro tagged with the
/// child label. Beyond preventing deadlock, this gives us a free debug
/// trail in Console.app whenever an OAuth flow misbehaves.
///
/// The task ends naturally on EOF (child closed the fd), so there's no
/// explicit cleanup — `tokio::spawn` detaches it and the runtime drops
/// it once `next_line()` returns `Ok(None)`.
fn drain_to_log<R>(child_label: &'static str, stream: &'static str, reader: R)
where
    R: AsyncRead + Unpin + Send + 'static,
{
    tokio::spawn(async move {
        let mut lines = BufReader::new(reader).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => log::info!("[{} {}] {}", child_label, stream, line),
                Ok(None) => break,
                Err(e) => {
                    log::warn!("[{} {}] drainer error: {}", child_label, stream, e);
                    break;
                }
            }
        }
    });
}

/// Build the PATH the bundled `claude` shim needs to be discoverable when
/// the .app is launched from Finder. Finder-launched apps inherit the
/// launchd minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`) which never
/// contains node/claude — without this prefix every spawn would fail with
/// "claude: command not found".
fn build_child_path() -> String {
    let parent_path = std::env::var("PATH").unwrap_or_default();
    let bundled_bins = resolve_bundled_node_bins();
    if bundled_bins.is_empty() {
        return parent_path;
    }
    let mut parts: Vec<String> = bundled_bins
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect();
    if !parent_path.is_empty() {
        parts.push(parent_path);
    }
    parts.join(":")
}

/// Spawn `claude auth login` with the augmented PATH and wait for the
/// user to complete (or cancel) the OAuth flow in the system browser.
/// Credentials are written to `~/.claude/.credentials.json` — shared by
/// both the bundled and any user-installed `claude` binary.
///
/// The child PID is recorded in `AppState::claude_login_pid` for the
/// duration of the await so `cancel_claude_login` (driven by the
/// frontend's 600s countdown) can SIGTERM the in-flight CLI without
/// having to hold the Child handle across the await boundary.
/// `kill_on_drop(true)` is a defense-in-depth: if the Tauri runtime
/// drops this future (e.g. on app quit while a login is mid-flight),
/// the child is SIGKILL'd rather than leaked as an orphan that keeps
/// the OAuth callback port bound forever.
#[tauri::command]
pub async fn trigger_claude_login(state: State<'_, AppState>) -> Result<String, String> {
    let child_path = build_child_path();
    let mut child = tokio::process::Command::new("claude")
        .args(["auth", "login"])
        .env("PATH", &child_path)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("Failed to spawn claude auth login: {}", e))?;

    if let Some(pid) = child.id() {
        if let Ok(mut guard) = state.claude_login_pid.lock() {
            *guard = Some(pid);
        }
    }

    // Drain stdio so a chatty future CLI release can't deadlock by
    // filling the 16 KB pipe buffer — see drain_to_log() rationale.
    if let Some(stdout) = child.stdout.take() {
        drain_to_log("claude-login", "stdout", stdout);
    }
    if let Some(stderr) = child.stderr.take() {
        drain_to_log("claude-login", "stderr", stderr);
    }

    let wait_result = child.wait().await;

    // Always clear the recorded PID — success, error, or cancel — so a
    // stale entry doesn't trick a later cancel into SIGTERMing some
    // unrelated process that happens to recycle this PID.
    if let Ok(mut guard) = state.claude_login_pid.lock() {
        *guard = None;
    }

    let status =
        wait_result.map_err(|e| format!("Failed waiting for claude auth login: {}", e))?;
    if status.success() {
        Ok("Login completed successfully".to_string())
    } else {
        Err(format!(
            "claude auth login exited with code {}",
            status.code().unwrap_or(-1)
        ))
    }
}

/// Send SIGTERM to the in-flight `claude auth login` child, if any.
///
/// Used by the frontend's 600s timeout: when the countdown reaches 0,
/// it calls this to abort the dangling login. The matching
/// `trigger_claude_login` future is still awaiting `child.wait()` —
/// SIGTERM causes `claude` to tear down its OAuth callback server and
/// exit non-zero, which the trigger surfaces back to the frontend as
/// an Err so the UI can revert to the logged-out state.
///
/// Returns `true` if a login was actually in flight and got the
/// signal, `false` if no PID was recorded (no-op).
#[tauri::command]
pub async fn cancel_claude_login(state: State<'_, AppState>) -> Result<bool, String> {
    let pid_opt = match state.claude_login_pid.lock() {
        Ok(g) => *g,
        Err(_) => return Err("claude_login_pid mutex poisoned".to_string()),
    };
    let Some(pid) = pid_opt else {
        return Ok(false);
    };

    #[cfg(unix)]
    {
        // Safety: kill() is safe to call with any pid_t; an invalid
        // one returns ESRCH which we ignore. SIGTERM lets the CLI
        // close its callback HTTP server cleanly. trigger_claude_login
        // will observe the exit and clear the PID itself.
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }

    Ok(true)
}

/// Spawn `claude auth logout` to revoke the locally cached OAuth
/// credentials. Symmetric to `trigger_claude_login`. Used by the settings
/// UI so users can switch accounts or sign out without touching a
/// terminal.
///
/// We spawn-then-wait (instead of `.status()`) for the same reason as
/// login: stdio is piped, so we attach `drain_to_log` to both pipes
/// before awaiting. Without drainers a verbose future logout could
/// deadlock filling the 16 KB pipe buffer.
#[tauri::command]
pub async fn trigger_claude_logout(_state: State<'_, AppState>) -> Result<String, String> {
    let child_path = build_child_path();
    let mut child = tokio::process::Command::new("claude")
        .args(["auth", "logout"])
        .env("PATH", &child_path)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| format!("Failed to spawn claude auth logout: {}", e))?;

    if let Some(stdout) = child.stdout.take() {
        drain_to_log("claude-logout", "stdout", stdout);
    }
    if let Some(stderr) = child.stderr.take() {
        drain_to_log("claude-logout", "stderr", stderr);
    }

    let status = child
        .wait()
        .await
        .map_err(|e| format!("Failed waiting for claude auth logout: {}", e))?;
    if status.success() {
        Ok("Logout completed".to_string())
    } else {
        Err(format!(
            "claude auth logout exited with code {}",
            status.code().unwrap_or(-1)
        ))
    }
}

/// Non-blocking status check — mirrors what the Python backend does at
/// `GET /api/providers/claude-status` but runs from the Tauri side so the
/// frontend can poll without waiting for the backend to be fully up.
#[tauri::command]
pub async fn get_claude_login_status(
    _state: State<'_, AppState>,
) -> Result<ClaudeLoginStatus, String> {
    let child_path = build_child_path();
    let mut status = ClaudeLoginStatus {
        cli_installed: false,
        logged_in: false,
    };

    let which_out = tokio::process::Command::new("which")
        .arg("claude")
        .env("PATH", &child_path)
        .output()
        .await;
    if let Ok(w) = &which_out {
        if w.status.success() {
            status.cli_installed = true;
        }
    }

    if !status.cli_installed {
        return Ok(status);
    }

    if let Ok(auth_out) = tokio::process::Command::new("claude")
        .args(["auth", "status"])
        .env("PATH", &child_path)
        .output()
        .await
    {
        if auth_out.status.success() {
            let stdout = String::from_utf8_lossy(&auth_out.stdout);
            if stdout.contains("\"loggedIn\":true") || stdout.contains("\"loggedIn\": true") {
                status.logged_in = true;
            }
        }
    }

    Ok(status)
}

#[derive(serde::Serialize, Clone)]
pub struct ClaudeLoginStatus {
    pub cli_installed: bool,
    pub logged_in: bool,
}
