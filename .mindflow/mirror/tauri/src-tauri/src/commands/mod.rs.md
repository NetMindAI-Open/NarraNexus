---
code_file: tauri/src-tauri/src/commands/mod.rs
last_verified: 2026-05-18
---

# mod.rs — Module declaration for the commands directory

Six lines: `pub mod auth; pub mod config; pub mod deep_link; pub mod health; pub mod service; pub mod tray;`.
No logic. Each module exposes one or more `#[tauri::command]` functions
that get registered in `lib.rs::run`'s `invoke_handler!`. `deep_link` was
added with the `narranexus://` one-click-install handoff.
