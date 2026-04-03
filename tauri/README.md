# NarraNexus Desktop (Tauri 2)

Tauri 2 desktop application shell for NarraNexus.

## Development

```bash
# Prerequisites: Rust toolchain, Node.js
cd tauri
cargo tauri dev
```

## Build

```bash
cargo tauri build
```

## Architecture

- `src-tauri/src/commands/` — IPC commands exposed to frontend via `invoke()`
- `src-tauri/src/sidecar/` — Python process lifecycle management
- `src-tauri/src/state.rs` — Global app state (config, process manager, health monitor)
- `src-tauri/src/tray.rs` — System tray menu
- Frontend loaded from `../frontend/dist`
