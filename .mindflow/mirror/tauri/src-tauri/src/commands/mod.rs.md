---
code_file: tauri/src-tauri/src/commands/mod.rs
last_verified: 2026-05-27
---

# mod.rs — Module declaration for the commands directory

Just module declarations: `auth`, `config`, `deep_link`, `health`,
`service`, `tray`, `updater`, `artifact_fetch`. No logic. Each module
exposes one or more `#[tauri::command]` functions registered in
`lib.rs::run`'s `invoke_handler!`. `deep_link` was added with the
`narranexus://` one-click-install handoff. `artifact_fetch` (2026-05-27)
proxies artifact bytes through Rust to dodge WKWebView's mixed-content
blocker in the dmg — see [[artifact_fetch.rs]] for the rationale.
