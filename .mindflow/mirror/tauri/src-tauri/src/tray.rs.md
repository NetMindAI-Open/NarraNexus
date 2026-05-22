---
code_file: tauri/src-tauri/src/tray.rs
last_verified: 2026-05-22
---

# tray.rs — System tray icon and menu

## 2026-05-22 — give the tray an icon (#2a, was blank)

`TrayIconBuilder::new()` was built with no `.icon(...)`, so the macOS menu-bar
tray rendered blank. Now it loads `icons/tray@2x.png` (embedded via
`include_bytes!`, no runtime path resolution) — the NarraNexus v2 mark as a
monochrome black+alpha **template** — and sets `.icon_as_template(true)` so
macOS tints it for light/dark menu bars. The template PNG is generated from
`docs/images/NarraNexusLogo_v2/narra-nexus-logo.svg` (full-fidelity headless-
Chrome render → black silhouette). The colored app icon set (icon.icns + pngs)
was regenerated from the same mark on a navy rounded-rect in the same change.

Creates a tray icon with three menu items: Start All Services, Stop All
Services, Quit. The "Start" and "Stop" items currently only log — the actual
service management is done at startup (auto-start) and shutdown
(window close event). Wiring the tray items to `start_all_services` /
`stop_all_services` commands is future work.

Called by `lib.rs::setup()`. Uses `tauri_plugin_shell` (indirectly via
Tauri builder).
