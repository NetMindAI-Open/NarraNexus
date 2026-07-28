---
code_file: tauri/src-tauri/src/commands/notify.rs
last_verified: 2026-07-23
stub: false
---

# notify.rs — OS notification command (#44)

`notify_completion(title, body)` posts a system notification via
tauri-plugin-notification's Rust API. Exists as a custom command (instead of
the plugin's JS guest bindings) because the frontend carries no
`@tauri-apps/*` npm dependency — bare specifiers break inside the packaged
DMG (see [[tauri.ts]]). All gating (desktop-only, window unfocused, not
user-cancelled) lives in the caller chain
[[desktopNotify.ts]] ← [[chatStore.ts]] `stopStreaming`. Best-effort: Err is
swallowed frontend-side. Plugin registered in [[lib.rs]]; capability
`notification:default` in capabilities/default.json.
