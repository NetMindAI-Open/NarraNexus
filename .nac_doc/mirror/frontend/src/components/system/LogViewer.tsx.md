---
code_file: frontend/src/components/system/LogViewer.tsx
last_verified: 2026-04-10
---

# LogViewer.tsx — Terminal-style log viewer with service filter tabs

Displays a capped, scrollable list of `LogEntry` records. Each entry shows
timestamp, service ID (color-coded), and the message (stderr lines rendered
in red). Filter tabs are dynamically derived from the unique service IDs in
the log array.

## Why it exists

The Tauri desktop app runs Python sidecars whose stdout/stderr is captured by
`process_manager.rs`. Without a UI viewer, users have no way to debug service
startup failures or agent errors from inside the app.

## Upstream / downstream

- **Upstream:** `LogEntry[]` from the parent page, sourced via Tauri
  `get_logs` command
- **Used by:** System page

## Design decisions

**Auto-scroll with manual override:** The viewer scrolls to the bottom on
every new entry unless the user has manually scrolled up. The "at bottom"
detection uses a 40px threshold to avoid fighting the user's scroll position
when they're just above the bottom.

**`maxEntries` cap (default 500):** The backend ring buffer also caps at 500.
If both are in sync, oldest entries are evicted consistently. If the frontend
cap is set lower than the backend, the viewer shows a truncated view.

**`std::sync::Mutex` for the log buffer:** The Rust side uses a standard
(non-async) mutex for log appends because drainer tasks never cross `await`
points when writing. This avoids deadlocking the outer `tokio::sync::Mutex`
that `ProcessManager` uses for service operations.

## Gotchas

Service color assignments (`SERVICE_COLORS`) are hardcoded by service ID.
Any new service ID not in the map gets the default `text-[var(--text-secondary)]`
color. Add the new service to `SERVICE_COLORS` when adding a new sidecar.
