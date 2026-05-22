---
code_file: tauri/src-tauri/src/sidecar/port_preflight.rs
last_verified: 2026-05-22
---

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
