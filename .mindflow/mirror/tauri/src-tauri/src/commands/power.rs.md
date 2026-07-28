---
code_file: tauri/src-tauri/src/commands/power.rs
last_verified: 2026-07-23
stub: false
---

# power.rs — Locked Use commands

`set_prevent_sleep(enabled)` / `get_prevent_sleep()`. macOS: spawns
`caffeinate -dims -w <our pid>` — `-w` ties the assertion to the app's
lifetime, so a crash/quit can never leave an orphan keeping the machine
awake (toggle-off kills the child early). Non-macOS returns Err so the
frontend toggle stays off. Child handle lives in the managed
`PreventSleepState` (registered in [[lib.rs]]). Frontend owner:
[[powerStore.ts]].
