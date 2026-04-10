---
code_file: frontend/src/stores/runtimeStore.ts
last_verified: 2026-04-10
stub: false
---

# runtimeStore.ts — App mode, feature flags, and base URL resolution

## Why it exists

The app can run in four distinct modes: `local` (bash/desktop, direct backend), `cloud-app` (user-supplied cloud server), `cloud-web` (Nginx-deployed build, same origin), and `null` (first launch, no mode chosen). Mode governs which features are available, whether auth requires a password, and where HTTP and WebSocket requests are sent. Centralizing this in a persisted store ensures the whole app sees the same mode without prop-drilling.

This file also exports the two critical functions `getApiBaseUrl()` and `getWsBaseUrl()` that every network caller resolves against. Making them live here (rather than in `api.ts` or `wsManager.ts`) means both REST and WebSocket code share a single source of truth.

## Upstream / Downstream

Persisted to `localStorage` under `narranexus-runtime`. Only `mode`, `userType`, and `cloudApiUrl` are persisted (via `partialize`). `features` is always derived fresh on hydration via the `merge` function.

Consumed by `App.tsx` (`ProtectedRoute`, `PublicRoute`, `RootRedirect` all read `mode`), `LoginPage.tsx` and `RegisterPage.tsx` (read `mode` to decide UI variant), `ModeSelectPage.tsx` (calls `setMode`, `setCloudApiUrl`), `api.ts` (imports `getApiBaseUrl`), `wsManager.ts` (imports `getWsBaseUrl`), and `SetupPage.tsx` (calls `getBaseUrl` via the re-exported alias).

## Design decisions

**`features` is always derived, never stored.** `deriveFeatures(mode, userType)` is called on every `setMode` and `setUserType` call, and inside the `merge` hydration hook. This guarantees features never drift out of sync with the underlying mode/userType pair. Storing `features` would require manual sync everywhere mode or userType changes.

**`getApiBaseUrl` resolution order (documented inline):**
1. `VITE_API_BASE_URL` env var — used by cloud-web Nginx builds where the frontend is co-served with the API.
2. Cloud mode + `cloudApiUrl` — the user-supplied server URL.
3. Tauri detection — fallback to `http://localhost:8000`.
4. Empty string — dev mode, Vite proxy handles `/api/*` routing.

**Tauri detection is intentionally duplicated.** `runtimeStore.ts` has its own `_detectTauri()` that checks `window.__TAURI__`, `window.__TAURI_INTERNALS__`, `tauri:` protocol, and `tauri.localhost` hostname. This avoids importing from `lib/platform.ts`, which would create a circular dependency chain through the store layer.

**`initialize` is a no-op.** It was part of an earlier design and is kept only so persisted state that stored an `initialize` key does not crash on hydration. The `@deprecated` JSDoc annotation signals it can be removed once all persisted states have naturally migrated.

**`setCloudApiUrl` strips trailing slashes.** Both `setCloudApiUrl` and `getApiBaseUrl` normalize by stripping trailing slashes so callers can always safely append `/api/...` without a double-slash risk.

## Gotchas

**Mode switch and router navigation race.** When the user clicks "Switch Mode" in the sidebar, the handler clears both `mode` (to null) and `isLoggedIn` (to false) in a single Zustand batch, then calls `navigate('/mode-select')`. `ProtectedRoute` re-renders before the navigation lands. It checks `!mode` FIRST (see `App.tsx` comments) so it redirects to `/mode-select` even if `isLoggedIn` and `mode` update in an interleaved order. Changing this check order would send the user to `/login` with a null mode, backing them with the wrong API URL.

**`cloud-web` forces mode on first visit.** `RootRedirect` checks `VITE_FORCE_CLOUD === 'true'` and calls `setMode('cloud-web')` if mode is not yet set. This skips the `/mode-select` page for hosted deployments. If `VITE_FORCE_CLOUD` is set in a local dev `.env`, every dev session will force cloud mode without prompting.

**`getWsBaseUrl` in dev mode reads `window.location.host`.** In development, the Vite dev server proxies `/ws/*` to the backend. The WebSocket URL must use the same host as the page (typically `localhost:5173`). If you access the dev server from a different machine (e.g., via IP), the derived WS URL will correctly use that machine's IP — but only if the Vite proxy config allows it.
