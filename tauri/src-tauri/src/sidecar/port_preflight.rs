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
// Fix direction:
//   This is stopgap #1 in a 3-step plan (see Lark Base TODO). Long-term
//   solution is dynamic-port allocation (stopgap #3), but that's a
//   multi-file refactor touching backend + frontend + MCP module config.
//   For now we detect the conflict before spawning anything and surface it
//   to the user with an actionable macOS native dialog.
//
// Design:
//   1. Try to bind :<port> on 127.0.0.1 for each port we need. If bind
//      succeeds, the port is free — we drop the listener immediately.
//   2. For every conflict, ask `lsof` who's holding the port.
//   3. If any conflict exists, render a `display dialog` AppleScript via
//      `osascript`, show it, then exit(1). The user gets a real error
//      instead of a silent black window.
//
// Deliberately uses std::net::TcpListener (not tokio) because this runs
// BEFORE Tauri's runtime spins up; all we need is a synchronous bind probe.

use std::net::TcpListener;
use std::process::Command;

/// Ports that must be free for NarraNexus to work. Kept in one place so
/// adding a service with a new port doesn't silently skip the preflight.
/// Source of truth:
///   8000  — backend uvicorn (state.rs ServiceDef "backend")
///   8100  — sqlite_proxy (state.rs ServiceDef "sqlite_proxy")
///   7801  — MCP server (module_runner.py, first module's port)
///   7830  — Lark trigger (run_lark_trigger)
/// 7802-7807 are additional MCP module ports but not every build spins
/// them up; we don't block launch on those.
pub const REQUIRED_PORTS: &[u16] = &[8000, 8100, 7801, 7830];

#[derive(Debug)]
pub struct PortConflict {
    pub port: u16,
    /// Human-readable description of who's holding the port, e.g.
    /// "Cursor (PID 55738)". None if `lsof` failed or isn't installed.
    pub holder: Option<String>,
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
                Some(PortConflict {
                    port,
                    holder: find_holder(port),
                })
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

fn find_holder(port: u16) -> Option<String> {
    // `lsof -nP -iTCP:<port> -sTCP:LISTEN` returns something like:
    //   COMMAND    PID  USER   FD   TYPE   DEVICE ...
    //   Cursor   55738  user   52u  IPv4   0x...
    //
    // We parse the second line (first data row). If there are multiple
    // holders (unusual but possible with SO_REUSEPORT), we report the first.
    let output = Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{}", port), "-sTCP:LISTEN"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let line = stdout.lines().nth(1)?;
    let mut parts = line.split_whitespace();
    let command = parts.next()?;
    let pid = parts.next()?;
    Some(format!("{} (PID {})", command, pid))
}

/// Render and show a macOS native dialog describing the conflicts, then
/// terminate the process with exit code 1.
///
/// We use osascript because:
///   - Tauri's dialog plugin requires the runtime + a window, which don't
///     exist this early in setup().
///   - osascript is always available on macOS (dmg is mac-only).
///   - `display dialog` renders a native Cocoa alert and blocks until the
///     user clicks the button, which is exactly what we want.
pub fn show_conflict_dialog_and_exit(conflicts: &[PortConflict]) -> ! {
    let mut msg = String::from("NarraNexus 无法启动\n\n以下端口被其他程序占用：\n\n");
    for c in conflicts {
        match &c.holder {
            Some(h) => msg.push_str(&format!("  • 端口 {} — 被 {} 占用\n", c.port, h)),
            None => msg.push_str(&format!("  • 端口 {} — 占用者未知\n", c.port)),
        }
    }
    msg.push_str("\n请关闭占用这些端口的程序后重新打开 NarraNexus。\n\n");
    msg.push_str("提示：如果占用者是 IDE（例如 Cursor / VS Code），");
    msg.push_str("可能是之前在 IDE 的终端里运行过 bash run.sh，子进程的 socket 仍绑在 IDE 上，");
    msg.push_str("重启 IDE 即可释放。");

    // Also log to stderr so terminal launches see the message.
    eprintln!("\n[NarraNexus] Port conflict detected:\n{}\n", msg);

    // AppleScript string quoting: backslash-escape double quotes. Actual
    // newline characters pass through fine (AppleScript strings allow them).
    let escaped = msg.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        r#"display dialog "{}" with title "NarraNexus" buttons {{"退出"}} default button "退出" with icon stop"#,
        escaped
    );

    // Best-effort dialog. If osascript is missing or the user kills it,
    // we still exit; they at least have the stderr message.
    let _ = Command::new("osascript").args(["-e", &script]).status();

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
