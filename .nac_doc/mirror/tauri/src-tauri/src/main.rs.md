---
code_file: tauri/src-tauri/src/main.rs
last_verified: 2026-04-10
---

# main.rs — Entry point, suppresses Windows console window

Four lines. The `#[cfg_attr(not(debug_assertions), windows_subsystem = "windows")]`
attribute prevents a terminal window appearing on Windows release builds.
All logic is in `narranexus_lib::run()`.
