---
code_file: frontend/src/lib/platform.ts
last_verified: 2026-05-28
stub: false
---

## 2026-05-28 — TauriBridge fully wired (was Phase-4 stub)

Every method on `TauriBridge` except `getLogs` used to `throw "Tauri
runtime not available"`. `SystemPage` therefore always rendered the
"Platform not available" empty state even in the dmg, even though
all the Rust `#[tauri::command]` functions were registered and
working. This wires them up:

- `getServiceStatus` → `invoke("get_service_status")`
- `getHealthStatus` (new) → `invoke("get_health_status")`
- `startAllServices` → `invoke("start_all_services")`
- `stopAllServices` → `invoke("stop_all_services")`
- `restartService(id)` → `invoke("restart_service", { serviceId: id })`
- `getAppMode` / `getAppConfig` → `invoke("get_app_mode" / "get_app_config")`
- `openExternal` → `invoke("plugin:shell|open", { path: url })`

Removed: `onHealthUpdate` and `onLog` subscription stubs — Rust does
not emit these events. [[SystemPage.tsx]] polls instead.

Also fixed a latent bug in the previous `getLogs` mapping: it accessed
`e.service_id` (snake_case) on a JSON payload that arrives camelCase
(Rust `LogEntry` has `#[serde(rename_all = "camelCase")]`), so every
log line silently lost its `serviceId`. Now declared `LogEntry[]`
directly with no remap.

# platform.ts — PlatformBridge abstraction for Tauri vs web

## Why it exists

`SystemPage.tsx` needs to display process health, control services, and stream logs. In the Tauri desktop app this works by calling into Rust-backed IPC. In a web browser there is no such capability. Rather than scattering `if (__TAURI__)` checks throughout the component, a `PlatformBridge` interface abstracts the detection. The component calls the same methods regardless of runtime.

## Upstream / Downstream

Exports the singleton `platform` which is either a `TauriBridge` or `WebBridge` instance, determined at module evaluation time by checking `window.__TAURI__`.

Used exclusively by `pages/SystemPage.tsx`. No other component in the current codebase calls `platform.*`.

## Design decisions

**All methods throw in `TauriBridge` currently.** The Tauri integration is marked as "Phase 4 — placeholder". The bridge is designed to accept future Tauri IPC calls without changing the `SystemPage` component. When Tauri support is implemented, only `TauriBridge` methods need filling in.

**`WebBridge` implements `isLocalMode()` as false and `getAppConfig()` returns cloud-web.** In cloud-web deployments `SystemPage` would normally be hidden via `runtimeStore.features.showSystemPage = false`. But if it were ever shown, `WebBridge` provides safe fallback values.

**`openExternal` is the only operational `WebBridge` method.** In web mode, `openExternal` uses `window.open`. All service management methods throw, which `SystemPage` catches and renders as a "Platform Not Available" placeholder.

## Gotchas

**`TauriBridge` is checked via `window.__TAURI__` only.** The more robust multi-signal detection (protocol, `__TAURI_INTERNALS__`, hostname) lives in `runtimeStore._detectTauri()`. The two implementations can diverge — if a new Tauri build adds a detection signal without updating both, `platform` may return `WebBridge` while `runtimeStore` correctly detects Tauri. The Tauri features on `SystemPage` would then silently fail.

**`platform` is evaluated at module load.** There is no lazy detection. If `window.__TAURI__` is not defined at parse time (possible in some Tauri initialization sequences), `WebBridge` is returned and the desktop app shows "Platform Not Available" even though Tauri is available.

**All `TauriBridge` methods throw `"Tauri runtime not available"`.** `SystemPage` catches exceptions from `platform.*` calls, so thrown errors are handled gracefully. But any code that calls `platform.*` without a try/catch will crash in Tauri mode until Phase 4 is implemented.
