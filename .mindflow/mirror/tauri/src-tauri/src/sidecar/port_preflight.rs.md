---
code_file: tauri/src-tauri/src/sidecar/port_preflight.rs
last_verified: 2026-05-27
---

## 2026-05-27 — REQUIRED_PORTS expanded to cover ALL sidecar ports

Real incident (Owner dmg, 2026-05-27 18:46): after force-quitting
the previous instance the next launch's port preflight passed (it
only checked 4 ports: 8000/8100/7801/7830) — but orphaned MCP
servers were still holding 7802-7808/7820/7831/7832 AND the
LarkTrigger health endpoint was still on 47831. Every MCP module
then failed to bind with `[Errno 48] address already in use`, the
MCP umbrella process shut down, and the desktop app silently lost
every MCP tool.

`REQUIRED_PORTS` now covers all 14 ports the sidecar stack binds
(8000/8100 + 7801/7802/7803/7804/7806/7807/7808/7820/7830/7831/7832
+ 47831). Combined with the `resolve_or_exit` orphan-cleanup, the
next launch now positively detects + offers to clean every port the
previous instance might have leaked, not just the "primary four".

## 2026-05-27 — orphan sidecar auto-cleanup (resolve_or_exit)

P0 from Owner: Force-Quit / app crash bypasses `ExitRequested`,
leaving Python sidecars holding 8000 / 8100. Next launch the user
sees the existing "port conflict, please close the other program"
dialog with no idea those processes are NarraNexus's own.

Solution: classify each conflict's PID against a curated list of
NarraNexus sidecar command-line patterns. If **all** conflicts are
our orphans, offer a single "Clean up & launch" dialog → SIGTERM
+ 1.5s wait + SIGKILL fallback → re-check ports → continue startup.
If **any** conflict is third-party, fall through to the existing
"close the other program" exit (we never auto-kill what we didn't
spawn).

API changes:
- `PortConflict` now exposes `pid: Option<u32>` + `command: Option<String>`
  + `is_our_orphan()` instead of the old `holder: Option<String>` flat
  string. `holder_label()` builds the human display when needed.
- New `resolve_or_exit(conflicts)` — primary entry point from
  `lib.rs::setup`. Either returns (after successful cleanup) or
  exits 1.
- Old `show_conflict_dialog_and_exit` kept as a deprecated shim that
  routes through `resolve_or_exit`, for back-compat with any caller
  that might still reference it.

Classifier heuristic (`is_narranexus_sidecar_cmdline`): cmdline
contains one of the curated module-launch markers (e.g.
`backend.main`, `xyz_agent_context.utils.sqlite_proxy`,
`xyz_agent_context.module.module_runner`, lark/slack/telegram
trigger modules) OR the bundled-python bundle-path
(`NarraNexus.app/Contents/Resources/resources/python`). Anything
outside this whitelist is treated as third-party.

Sanity unit tests in `#[cfg(test)] mod tests` cover the classifier
(recognises all sidecar shapes, rejects third-party python /
jupyter / django / node) and the `holder_label()` formatting.

## 2026-05-22 — added show_startup_failure_dialog_and_exit

New sibling to `show_conflict_dialog_and_exit`: same osascript native-dialog +
`exit(1)`, but for when a REQUIRED sidecar spawned yet never became ready (port
never bound / crashed on startup — bundled python blocked by Gatekeeper, an
Intel-Mac arch mismatch, or an unwritable `~/.narranexus/logs` killing the DB).
`lib.rs` calls it with the detailed message from `ProcessManager::startup_error`
(service + reason + log path + output tail) instead of the old silent
`log::error!`. The dialog appends a common-causes hint (Apple-Silicon-only /
Gatekeeper / first-run init).

# port_preflight.rs — detect hardcoded-port conflicts before spawning sidecars

## Intent

Every Python sidecar binds a hardcoded port:

  | Port | Service       |
  |------|---------------|
  | 8000 | backend       |
  | 8100 | sqlite_proxy  |
  | 7801 | MCP server    |
  | 7830 | lark_trigger  |

On a developer machine any of these — especially 8000 — is very likely
already held by something else (Django / Flask / Jupyter / a prior run.sh
that got reparented to an IDE terminal). When that happens, `spawn`
succeeds but the child dies instantly after bind fails; `process_manager`
doesn't know to escalate this to the user and the UI just sits on a black
loading screen with nothing in any log visible to the user.

This module runs first thing in `setup()` and refuses to start if any of
those ports is taken. The user gets an actionable native dialog instead
of a broken UI.

## Why not use Tauri's dialog plugin

`setup()` fires before the runtime spins a window. Tauri's dialog plugin
wants a `WebviewWindow` handle, which we don't have yet. `osascript
display dialog` renders a native Cocoa alert synchronously without any
window prerequisite and is always available on macOS (dmg is mac-only).

## Staged plan this implements

Entry #1 in a 3-step plan recorded in the Lark Base TODO tracker:

1. **Detect + dialog** (this file) — stopgap; ports remain hardcoded.
2. **Move to high ports (18xxx / 17xxx)** — lowers collision probability
   by an order of magnitude; still hardcoded.
3. **Dynamic port allocation** — bind to port 0, write the OS-assigned
   port to `~/.narranexus/ports.json`, have every other service read it
   from there. True zero-conflict solution, but touches backend, frontend,
   and MCP module config.

## Upstream / downstream

- **Called by:** `lib.rs::run()` as the first step inside `setup()`
- **Depends on:** system `lsof` (optional, improves error message),
  `osascript` (always present on macOS)
- **On conflict:** calls `std::process::exit(1)` — no recovery path by design
