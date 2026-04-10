---
code_file: tauri/src-tauri/src/commands/config.rs
last_verified: 2026-04-10
---

# config.rs — IPC commands for app configuration and mode switching

Three commands:
- `get_app_config` → full `AppConfig` struct (mode, user_type, api_base_url,
  python_path, db_path)
- `get_app_mode` → `"local"` or `"cloud-app"` string
- `set_app_mode(mode)` → mutates `AppState.config.mode`

Mode switching (`set_app_mode`) only updates the Rust-side config — it does
not restart services or reload the frontend. The frontend must handle its own
side effects (hard reload, clear localStorage) after calling this command.
This is why the frontend's mode-switch logic calls `window.location.reload()`
after `set_app_mode` (commit `27c394e`).

## Gotchas

`set_app_mode` accepts `"local"` and `"cloud-app"` only. Any other string
returns an error. The frontend must use these exact string values.
