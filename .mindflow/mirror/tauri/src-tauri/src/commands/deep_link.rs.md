---
code_file: tauri/src-tauri/src/commands/deep_link.rs
last_verified: 2026-05-18
stub: false
---

# commands/deep_link.rs — cold-start URL drain

Single `#[tauri::command] consume_pending_deep_link` that returns and
clears `AppState::pending_deep_link`. Frontend calls it once on App
mount via the wrapper in `frontend/src/lib/tauri.ts::consumePendingDeepLink`.

## Why exists

Tauri's event channel does not queue events for not-yet-attached listeners.
A `narranexus://install?...` URL the OS hands us at cold start fires
`on_open_url` inside `lib.rs::setup` long before React renders and
attaches its `deep-link-received` listener — those events are silently
lost. The on_open_url callback therefore writes the URL into
`AppState::pending_deep_link`; the frontend's mount-time `consume_*`
call returns and clears it (single-shot — `take()`). Hot URLs (app
already running) take the event channel as usual.

## Why command, not a static getter

`#[tauri::command]` ensures the IPC bridge handles capability checks
(deep-link plugin perms granted in `capabilities/default.json`) and
serialization automatically. The frontend just does
`invoke('consume_pending_deep_link')`.
