---
code_file: tauri/src-tauri/src/tray.rs
last_verified: 2026-05-27
---

## 2026-05-27 — tray item doubles as the updater state surface

The tray entry was renamed `updater_action` (was `check_updates`) and
now serves three verbs from one menu item, chosen by the current
updater state machine value (see [[updater.rs]]):

| state          | label                                  | click does          |
|----------------|----------------------------------------|---------------------|
| `idle`         | `Check for Updates…`                   | kick pipeline       |
| `up_to_date`   | `Up to date (1.7.10)`                  | kick pipeline       |
| `checking`     | `Checking for updates…`                | no-op (in-flight)   |
| `available`    | `Update 1.7.11 found — downloading…`   | no-op (in-flight)   |
| `downloading`  | `Downloading update (35%)`             | no-op (in-flight)   |
| `installing`   | `Installing 1.7.11…`                   | no-op (in-flight)   |
| `ready`        | `Restart to apply 1.7.11`              | `app.restart()`     |
| `failed`       | `Check for Updates… (last check failed)` | kick pipeline     |

The label is rewritten live by a Rust-side `app.listen("updater:state", ...)`
subscription installed in `create_tray`. The pipeline's reentrancy
guard ensures concurrent clicks don't pile up; we still no-op the
click in the four in-flight states for clarity (user shouldn't have
to wonder whether clicking again does anything).

`updater_label(state)` is the pure-function mapping. Side-effect free
on purpose so the live `set_text` from the event listener stays
trivial.

## 2026-05-27 — "Check for Updates…" menu item (initial wiring)

Added a fourth tray menu item between Stop All Services and Quit, with
a separator on each side: `Check for Updates…`. The 2026-05-27 unified
state-machine rewrite above replaced the original click handler (which
spawned the deprecated `run_manual_update_check`) with state-aware
dispatch.

Why a tray entry and not an app-menu entry: macOS apps conventionally
hide "Check for Updates" under both, but the tray menu is what the
user can see from any screen state without focusing the window, and
adding an app-menu in Tauri v2 requires setting up the whole
`Menu::default_macos_main` scaffold which we don't yet have. Tray
entry is the minimum step. A proper app menu can come later as a
separate refactor.

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

**Gotcha**: `Image::from_bytes` is cfg-gated behind Tauri's `image-ico` /
`image-png` features. The `tauri` dep in `Cargo.toml` must keep `image-png`
enabled — drop it and `from_bytes` silently disappears and the build fails
with E0599 (this broke the dmg CI on 2026-05-22).
