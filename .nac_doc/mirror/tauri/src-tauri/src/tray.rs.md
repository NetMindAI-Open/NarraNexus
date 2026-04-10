---
code_file: tauri/src-tauri/src/tray.rs
last_verified: 2026-04-10
---

# tray.rs — System tray icon and menu

Creates a tray icon with three menu items: Start All Services, Stop All
Services, Quit. The "Start" and "Stop" items currently only log — the actual
service management is done at startup (auto-start) and shutdown
(window close event). Wiring the tray items to `start_all_services` /
`stop_all_services` commands is future work.

Called by `lib.rs::setup()`. Uses `tauri_plugin_shell` (indirectly via
Tauri builder).
