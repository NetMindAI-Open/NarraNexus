---
code_file: frontend/src/main.tsx
last_verified: 2026-06-16
stub: false
---

## 2026-06-16 — inbound entry capture moved pre-render

`captureInboundEntry()` (from `lib/netmindAuth/tokenInbound`) now runs
once at boot, before `createRoot().render()`, alongside the Manyfold
fragment-auth bootstrap. It reads `?token` / `?source` from the TRUE
entry URL and stashes `source` into sessionStorage. This MUST happen
before the first render: for a logged-out arena entry (`/?source=arena`)
the `RootRedirect → <Navigate to="/login">` effect (a descendant of App)
fires before App's own mount effect, rewriting the URL and dropping the
param. Reading it in main.tsx beats that race. See [[tokenInbound]].

## 2026-05-27 — externalLinkInterceptor wire-up

`installExternalLinkInterceptor()` runs once at boot, immediately
after the Manyfold fragment-auth bootstrap. In Tauri it captures
`<a target="_blank">` clicks app-wide and routes them through
plugin-shell so the OS browser opens. In browser mode the install
is a no-op so the default `target="_blank"` behavior is untouched.
See [[externalLinkInterceptor]] for the rationale (TODO
2026-05-27-dmg-external-links-dead.md).

# main.tsx — React app bootstrap

## Why it exists

The Vite entry point that mounts the React tree. Sets up three global providers that wrap the entire app: `StrictMode`, `QueryClientProvider` (TanStack Query), and `BrowserRouter`.

## Upstream / Downstream

Entry point for the Vite bundler. Renders `App.tsx` as the root component.

## Design decisions

**TanStack Query config.** `staleTime: 30_000` — cached data is considered fresh for 30 seconds, preventing redundant refetches on rapid navigation. `retry: 1` — one retry on failure, avoiding infinite retry loops on persistent errors. `refetchOnWindowFocus: false` — disabled to avoid surprise refetches when the user alt-tabs back; `useAutoRefresh` handles explicit background refresh instead.

**`BrowserRouter` (not `HashRouter`).** The app uses clean paths (`/login`, `/app/chat`). This requires the server to serve `index.html` for all paths — handled by Vite's dev server and Nginx in production. Hash-based routing would have worked but is less clean.

**`StrictMode` is on in development.** React's StrictMode mounts components twice and may surface issues with effects that run more than once. This is intentional — catching bugs early. The known side effect is `wsManager.run` being invoked twice during dev; `wsManager.close` on the second call handles this cleanly.

**NM ECharts theme registration.** Side-effect import `./lib/echarts-nm-theme` runs `registerNMEChartsTheme()` at startup, registering the `nm-light` and `nm-dark` ECharts themes before any chart component mounts. Placed between `./index.css` and `App.tsx` so the visual baseline (CSS) loads first, then the chart theme, then the React tree — ensuring no chart can render before its theme is available.

## Gotchas

**QueryClient is a module-level singleton.** It is created outside the component tree, so it is shared across HMR reloads in development. If a TanStack Query cache becomes stale during development, a full page refresh (not just HMR) is needed to get a fresh client.
