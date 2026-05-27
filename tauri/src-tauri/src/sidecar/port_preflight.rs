// Pre-launch port conflict detector.
//
// Problem:
//   Every sidecar service binds a hardcoded port (backend 8000, sqlite_proxy
//   8100, MCP 7801, lark_trigger 7830). If any of those ports is already held
//   by another process — very common for :8000 because every Django / Flask /
//   Jupyter workflow binds it — the Python service fails to bind, exits
//   immediately after spawn, and the user sees "black screen loading forever"
//   with nothing in any visible log.
//
//   Even more common in practice (2026-05-27 incident): the user's PREVIOUS
//   NarraNexus instance was Force-Quit (Activity Monitor) or crashed. When
//   the parent app dies via SIGKILL it can't run its ExitRequested handler,
//   so the Python children orphan and keep holding the ports. Next launch
//   the user sees the port-conflict dialog and has no idea those processes
//   were started by NarraNexus itself.
//
// Fix direction:
//   This is stopgap #1 in a 3-step plan (see Lark Base TODO). Long-term
//   solution is dynamic-port allocation (stopgap #3), but that's a
//   multi-file refactor touching backend + frontend + MCP module config.
//   For now we detect the conflict before spawning anything; if EVERY
//   conflict is one of our own orphaned sidecars we offer to clean them
//   up in-place (single confirm dialog → kill → continue launch).
//
// Design:
//   1. Try to bind :<port> on 127.0.0.1 for each port we need. If bind
//      succeeds, the port is free — we drop the listener immediately.
//   2. For every conflict, ask `lsof` who's holding the port → PID + name.
//   3. For every PID, classify as "our orphan" (matches the sidecar
//      command-line patterns we spawn) or "third-party" (anything else).
//   4. If ALL conflicts are our orphans → show a "Clean up & continue"
//      / "Quit" dialog. On confirm, SIGTERM the orphans, give 1s, then
//      SIGKILL, re-check ports, proceed if clear.
//   5. If ANY conflict is third-party → show the "please close the other
//      program" dialog and exit (existing behaviour). We refuse to kill
//      processes the user didn't ask us to.
//
// Deliberately uses std::net::TcpListener (not tokio) because this runs
// BEFORE Tauri's runtime spins up; all we need is a synchronous bind probe.

use std::net::TcpListener;
use std::process::Command;

/// Ports that must be free for NarraNexus to work. Kept in one place so
/// adding a service with a new port doesn't silently skip the preflight.
/// Source of truth (all in state.rs ServiceDef / module_runner.py /
/// run_*_trigger.py):
///   8000   — backend uvicorn
///   8100   — sqlite_proxy
///   7801   — MCP AwarenessModule
///   7802   — MCP SocialNetworkModule
///   7803   — MCP JobModule
///   7804   — MCP ChatModule
///   7806   — MCP SkillModule (7805 retired, leave a gap)
///   7807   — MCP CommonToolsModule
///   7808   — MCP BasicInfoModule
///   7820   — MCP MessageBusModule
///   7830   — MCP LarkModule (+ LarkTrigger SDK subscriber)
///   7831   — MCP SlackModule
///   7832   — MCP TelegramModule
///   47831  — LarkTrigger health endpoint (_health_server.py)
///
/// History (2026-05-27): the list used to be only `[8000, 8100, 7801,
/// 7830]`. A real incident with the Owner showed that when a
/// previous-launch force-quit left orphaned MCP servers + lark health
/// listener holding 7802-7832 + 47831, the preflight passed (only 4
/// ports checked) but every MCP module then failed to bind with
/// `[Errno 48] address already in use`, the MCP umbrella process
/// shut down, and the desktop app silently lost every MCP tool. The
/// expanded list pairs with `resolve_or_exit`'s orphan-cleanup so the
/// next launch auto-recovers from every sidecar port, not just the
/// "primary four".
pub const REQUIRED_PORTS: &[u16] = &[
    8000, 8100,                                       // backend + sqlite proxy
    7801, 7802, 7803, 7804, 7806, 7807, 7808, 7820, // MCP modules
    7830, 7831, 7832,                                // channel MCP modules
    47831,                                            // LarkTrigger health endpoint
];

#[derive(Debug, Clone)]
pub struct PortConflict {
    pub port: u16,
    /// PID of the holding process. None if `lsof` failed or isn't installed
    /// (in which case we can't classify or auto-clean — fall back to the
    /// generic "please close it" dialog).
    pub pid: Option<u32>,
    /// Command name from `lsof` (e.g. "python3", "Cursor"). Cheap lookup,
    /// used for the user-facing message. Full cmdline (for classification)
    /// comes from `ps -p <pid> -o command=` lazily.
    pub command: Option<String>,
}

impl PortConflict {
    /// Human-readable description used in the dialog, e.g.
    /// "python3 (PID 21021)".
    pub fn holder_label(&self) -> String {
        match (&self.command, self.pid) {
            (Some(c), Some(p)) => format!("{} (PID {})", c, p),
            (Some(c), None) => c.clone(),
            (None, Some(p)) => format!("PID {}", p),
            (None, None) => "unknown".to_string(),
        }
    }

    /// True iff the holder's full command-line matches one of the sidecar
    /// command-lines NarraNexus itself spawns — i.e. it's an orphan from a
    /// previous instance and safe for us to kill on the user's confirmation.
    /// Returns false for any third-party process or for unidentifiable PIDs
    /// (we never auto-kill something we can't positively identify).
    pub fn is_our_orphan(&self) -> bool {
        let Some(pid) = self.pid else { return false };
        let Some(cmdline) = process_cmdline(pid) else { return false };
        is_narranexus_sidecar_cmdline(&cmdline)
    }
}

/// Probe every required port. Returns the set of conflicts, empty if all
/// ports are available.
pub fn check_required_ports() -> Vec<PortConflict> {
    REQUIRED_PORTS
        .iter()
        .filter_map(|&port| {
            if can_bind(port) {
                None
            } else {
                let (pid, command) = find_holder(port);
                Some(PortConflict { port, pid, command })
            }
        })
        .collect()
}

fn can_bind(port: u16) -> bool {
    // We only need loopback since every sidecar binds 127.0.0.1; a free
    // 127.0.0.1:port is all that matters. Using 0.0.0.0 would over-report
    // conflicts on machines with firewall rules on external interfaces.
    match TcpListener::bind(("127.0.0.1", port)) {
        Ok(listener) => {
            // Explicitly drop so the OS releases the port before we return.
            // TcpListener::drop closes the fd synchronously.
            drop(listener);
            true
        }
        Err(_) => false,
    }
}

/// Returns (PID, COMMAND) from lsof for the port's listener, or (None, None)
/// if lsof failed / nothing's listening / parse failed. Split out from the
/// previous string-returning version so we can classify by PID later.
fn find_holder(port: u16) -> (Option<u32>, Option<String>) {
    // `lsof -nP -iTCP:<port> -sTCP:LISTEN` returns something like:
    //   COMMAND    PID  USER   FD   TYPE   DEVICE ...
    //   Cursor   55738  user   52u  IPv4   0x...
    //
    // We parse the second line (first data row). If there are multiple
    // holders (unusual but possible with SO_REUSEPORT), we report the first.
    let output = match Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{}", port), "-sTCP:LISTEN"])
        .output()
    {
        Ok(o) if o.status.success() => o,
        _ => return (None, None),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let Some(line) = stdout.lines().nth(1) else { return (None, None) };
    let mut parts = line.split_whitespace();
    let command = parts.next().map(|s| s.to_string());
    let pid = parts.next().and_then(|s| s.parse::<u32>().ok());
    (pid, command)
}

/// Read the full command-line for a PID via `ps -p <pid> -o command=`.
/// macOS `ps` truncates by default; `-c` removes the leading process name
/// only, not the args, so `-o command=` is enough.
fn process_cmdline(pid: u32) -> Option<String> {
    let out = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() { None } else { Some(s) }
}

/// True iff the given command-line is something this app would have spawned
/// as one of its sidecars. The patterns intentionally match BOTH bundled-
/// python paths (resources/python/.../python3) AND module-launch fragments
/// (`-m backend.main`, `-m xyz_agent_context.utils.sqlite_proxy`, etc.) so
/// the heuristic catches dmg-bundled spawns *and* `bash run.sh` dev-mode
/// spawns alike. Anything outside this whitelist is treated as third-party
/// and never auto-killed.
fn is_narranexus_sidecar_cmdline(cmdline: &str) -> bool {
    const SIDECAR_MARKERS: &[&str] = &[
        // Module launches (most reliable — backend / sqlite_proxy / mcp /
        // lark_trigger / job_trigger / message_bus / slack / telegram all
        // launch via `python -m <one of these>`).
        "backend.main",
        "xyz_agent_context.utils.sqlite_proxy",
        "xyz_agent_context.module.module_runner",
        "xyz_agent_context.module.lark_module.run_lark_trigger",
        "xyz_agent_context.module.slack_module.run_slack_trigger",
        "xyz_agent_context.module.telegram_module.run_telegram_trigger",
        "xyz_agent_context.utils.run_module_poller",
        "xyz_agent_context.utils.run_job_trigger",
        "xyz_agent_context.utils.run_message_bus_trigger",
        // Uvicorn invocation: when the backend is spawned as
        // `uvicorn backend.main:app` instead of `python -m backend.main`.
        "uvicorn backend.main",
        // Bundled Python interpreter path inside the app bundle. Catches
        // any sidecar that uses the bundled python regardless of cmdline.
        "NarraNexus.app/Contents/Resources/resources/python",
    ];
    SIDECAR_MARKERS.iter().any(|m| cmdline.contains(m))
}

/// SIGTERM the PID, wait up to `wait_ms`, SIGKILL if still alive. Returns
/// true if the process is dead after the procedure.
#[cfg(unix)]
fn try_terminate_pid(pid: u32, wait_ms: u64) -> bool {
    // Best-effort SIGTERM — lets the child run its own cleanup
    // (loguru flush, DB close, ws disconnect).
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
    let poll_interval = std::time::Duration::from_millis(100);
    let deadline = std::time::Instant::now() + std::time::Duration::from_millis(wait_ms);
    while std::time::Instant::now() < deadline {
        if !pid_alive(pid) {
            return true;
        }
        std::thread::sleep(poll_interval);
    }
    // Still alive → SIGKILL
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }
    std::thread::sleep(std::time::Duration::from_millis(200));
    !pid_alive(pid)
}

#[cfg(not(unix))]
fn try_terminate_pid(_pid: u32, _wait_ms: u64) -> bool {
    // Windows isn't a target for NarraNexus dmg, but stub kept so the
    // module compiles cross-platform for tests.
    false
}

#[cfg(unix)]
fn pid_alive(pid: u32) -> bool {
    // `kill -0` returns 0 if the process exists and we can signal it,
    // ESRCH (3) if it doesn't.
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(not(unix))]
fn pid_alive(_pid: u32) -> bool {
    true
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifier_recognises_uvicorn_backend() {
        let cmd = "uvicorn backend.main:app --host 0.0.0.0 --port 8000";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_recognises_module_main_backend() {
        let cmd = "/Applications/NarraNexus.app/Contents/Resources/resources/python/bin/python3 \
                   -m backend.main";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_recognises_sqlite_proxy() {
        let cmd = "python3 -m xyz_agent_context.utils.sqlite_proxy --port 8100";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_recognises_module_runner_mcp() {
        let cmd = "python3 -m xyz_agent_context.module.module_runner mcp";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_recognises_lark_trigger() {
        let cmd = "python3 -m xyz_agent_context.module.lark_module.run_lark_trigger";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_recognises_slack_telegram_triggers() {
        assert!(is_narranexus_sidecar_cmdline(
            "python3 -m xyz_agent_context.module.slack_module.run_slack_trigger"
        ));
        assert!(is_narranexus_sidecar_cmdline(
            "python3 -m xyz_agent_context.module.telegram_module.run_telegram_trigger"
        ));
    }

    #[test]
    fn classifier_recognises_bundled_python_path() {
        // Any sidecar launched from the bundled python — even if we don't
        // recognise the module — gets classified as ours via the bundle
        // path marker. This is intentional belt-and-braces.
        let cmd = "/Applications/NarraNexus.app/Contents/Resources/resources/python/bin/python3 \
                   /some/script/we/havent/seen.py";
        assert!(is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_rejects_third_party_jupyter() {
        let cmd = "/usr/local/bin/python3 -m jupyterlab --port 8000";
        assert!(!is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_rejects_third_party_django() {
        let cmd = "python3 manage.py runserver 0.0.0.0:8000";
        assert!(!is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_rejects_unrelated_node() {
        let cmd = "node /Users/dev/some-tool/server.js --port 8000";
        assert!(!is_narranexus_sidecar_cmdline(cmd));
    }

    #[test]
    fn classifier_rejects_empty_and_short() {
        assert!(!is_narranexus_sidecar_cmdline(""));
        assert!(!is_narranexus_sidecar_cmdline("python3"));
        assert!(!is_narranexus_sidecar_cmdline("uvicorn"));
    }

    #[test]
    fn port_conflict_holder_label_shapes() {
        let c1 = PortConflict {
            port: 8000,
            pid: Some(21021),
            command: Some("python3".into()),
        };
        assert_eq!(c1.holder_label(), "python3 (PID 21021)");

        let c2 = PortConflict {
            port: 8000,
            pid: Some(99),
            command: None,
        };
        assert_eq!(c2.holder_label(), "PID 99");

        let c3 = PortConflict {
            port: 8000,
            pid: None,
            command: Some("Cursor".into()),
        };
        assert_eq!(c3.holder_label(), "Cursor");

        let c4 = PortConflict {
            port: 8000,
            pid: None,
            command: None,
        };
        assert_eq!(c4.holder_label(), "unknown");
    }
}

/// Resolve port conflicts before NarraNexus starts. Either:
///   (a) all conflicts are our own orphan sidecars from a previous force-
///       quit / crash → show a confirm dialog → on Yes, kill the orphans
///       and return (caller proceeds to normal startup);
///   (b) at least one conflict is a third-party process → show the
///       "please close the other program" dialog and exit(1).
///
/// Called by `lib.rs::setup` after `check_required_ports()` returns a
/// non-empty list. This function only returns in case (a) after the
/// re-check confirms the ports are now free; otherwise it exits.
///
/// We use osascript for dialogs because:
///   - Tauri's dialog plugin requires the runtime + a window, which don't
///     exist this early in setup().
///   - osascript is always available on macOS (dmg is mac-only).
///   - `display dialog` renders a native Cocoa alert and blocks until the
///     user clicks the button, which is exactly what we want.
pub fn resolve_or_exit(conflicts: &[PortConflict]) {
    // Classify: are ALL conflicts our own orphans?
    let classified: Vec<(PortConflict, bool)> = conflicts
        .iter()
        .map(|c| {
            let ours = c.is_our_orphan();
            (c.clone(), ours)
        })
        .collect();

    let third_party_present = classified.iter().any(|(_, ours)| !ours);

    if third_party_present {
        show_third_party_conflict_and_exit(conflicts);
    }

    // All conflicts are our orphans → offer auto-cleanup.
    if offer_orphan_cleanup_and_perform(&classified) {
        // Re-check after cleanup. If still conflicting, fall through to
        // the third-party exit (something we couldn't kill or new
        // process bound the freed port).
        let remaining = check_required_ports();
        if remaining.is_empty() {
            log::info!(
                "Cleaned up {} orphaned sidecar process(es) — continuing startup",
                classified.len()
            );
            return;
        }
        eprintln!(
            "[NarraNexus] Cleanup ran but ports still held: {:?}",
            remaining.iter().map(|c| c.port).collect::<Vec<_>>()
        );
        show_third_party_conflict_and_exit(&remaining);
    }

    // User declined cleanup → exit cleanly with no extra dialog (the
    // cleanup dialog already explained what would happen).
    std::process::exit(1);
}

/// Show "your previous NarraNexus didn't shut down cleanly" dialog with
/// "Clean up & launch" / "Quit" buttons. Returns true if the user
/// confirmed and cleanup was attempted (caller should re-check ports).
fn offer_orphan_cleanup_and_perform(classified: &[(PortConflict, bool)]) -> bool {
    let mut msg = String::from(
        "Your previous NarraNexus did not shut down cleanly.\n\n\
         The following background services are still running from \
         the previous launch and are holding ports NarraNexus needs:\n\n",
    );
    for (c, _) in classified {
        msg.push_str(&format!("  • Port {} — {}\n", c.port, c.holder_label()));
    }
    msg.push_str(
        "\nThese were started by NarraNexus itself. Click \"Clean up & launch\" to \
         terminate them and continue starting the app.",
    );

    let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus" buttons {{"Quit", "Clean up & launch"}} default button "Clean up & launch" with icon caution"#,
        escaped
    );
    let output = Command::new("osascript").args(["-e", &script]).output();
    let confirmed = match output {
        Ok(out) => String::from_utf8_lossy(&out.stdout).contains("Clean up & launch"),
        Err(_) => false,
    };
    if !confirmed {
        return false;
    }

    // Kill each orphan. 1.5s SIGTERM grace per process — long enough for
    // Python's atexit / loguru flush, short enough not to make the user
    // wait forever.
    for (c, _) in classified {
        if let Some(pid) = c.pid {
            let dead = try_terminate_pid(pid, 1500);
            if dead {
                log::info!("Terminated orphan sidecar pid={} (port={})", pid, c.port);
            } else {
                log::warn!(
                    "Failed to fully terminate orphan sidecar pid={} (port={})",
                    pid,
                    c.port
                );
            }
        }
    }
    true
}

/// Generic "another program is holding the port" dialog + exit.
/// Triggered when at least one conflict is a third-party process we won't
/// touch, or when auto-cleanup ran but the port is still held afterwards.
fn show_third_party_conflict_and_exit(conflicts: &[PortConflict]) -> ! {
    let mut msg = String::from("NarraNexus cannot start.\n\nThe following ports are in use by another program:\n\n");
    for c in conflicts {
        msg.push_str(&format!("  • Port {} — held by {}\n", c.port, c.holder_label()));
    }
    msg.push_str(
        "\nPlease close the program holding the port and re-launch NarraNexus.\n\n\
         Tip: if the holder is an IDE (Cursor / VS Code) you previously used to \
         run `bash run.sh`, the subprocess socket may still be bound to the IDE — \
         restarting the IDE releases it.",
    );

    eprintln!("\n[NarraNexus] Port conflict detected:\n{}\n", msg);

    let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus" buttons {{"Quit"}} default button "Quit" with icon stop"#,
        escaped
    );
    let _ = Command::new("osascript").args(["-e", &script]).status();

    std::process::exit(1);
}

/// Back-compat shim: old callers used `show_conflict_dialog_and_exit`.
/// Routes through the new `resolve_or_exit` path, which decides whether
/// to offer cleanup or treat as third-party.
#[deprecated(note = "Use resolve_or_exit, which handles both orphan cleanup and third-party conflicts")]
pub fn show_conflict_dialog_and_exit(conflicts: &[PortConflict]) -> ! {
    resolve_or_exit(conflicts);
    // resolve_or_exit either returns (after cleanup) or exits; we can't
    // return here because the signature is `-> !`. If it returned, we
    // still need to exit because back-compat callers won't proceed with
    // launch on their own.
    std::process::exit(1);
}

/// Show a detailed dialog when a REQUIRED sidecar fails its readiness check
/// (port never bound / process crashed on startup), then exit. Same osascript
/// mechanism as the port-conflict dialog. `detail` is the message built by
/// `ProcessManager::startup_error` (service, reason, log path, output tail).
///
/// This replaces the old silent `log::error!` on `start_all` failure — a fresh
/// machine where the bundled python is blocked (Gatekeeper / arch mismatch) or
/// a port-race leaves the backend down used to just show the UI and fail every
/// request with "Connection failed", with nothing to point the user at.
pub fn show_startup_failure_dialog_and_exit(detail: &str) -> ! {
    let mut msg = String::from("NarraNexus 启动失败\n\n一个必需的后台服务没能起来：\n\n");
    msg.push_str(detail);
    msg.push_str(
        "\n\n常见原因：\n  • 本版本仅支持 Apple Silicon（M 系列）芯片，Intel Mac 无法运行\n  \
         • macOS 安全策略(Gatekeeper)拦截了内置 Python（全新下载的 App 容易遇到）\n  \
         • 首次初始化失败\n\n请把上面的「Log file」发给我们排查。",
    );

    // Full detail to stderr / terminal launches.
    eprintln!("\n[NarraNexus] Startup failure:\n{}\n", msg);

    let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus" buttons {{"退出"}} default button "退出" with icon stop"#,
        escaped
    );
    let _ = Command::new("osascript").args(["-e", &script]).status();

    std::process::exit(1);
}
