---
code_file: frontend/src/main.tsx
last_verified: 2026-04-10
stub: false
---

# main.tsx — React app bootstrap

## Why it exists

The Vite entry point that mounts the React tree. Sets up three global providers that wrap the entire app: `StrictMode`, `QueryClientProvider` (TanStack Query), and `BrowserRouter`.

## Upstream / Downstream

Entry point for the Vite bundler. Renders `App.tsx` as the root component.

## Design decisions

**TanStack Query config.** `staleTime: 30_000` — cached data is considered fresh for 30 seconds, preventing redundant refetches on rapid navigation. `retry: 1` — one retry on failure, avoiding infinite retry loops on persistent errors. `refetchOnWindowFocus: false` — disabled to avoid surprise refetches when the user alt-tabs back; `useAutoRefresh` handles explicit background refresh instead.

**`BrowserRouter` (not `HashRouter`).** The app uses clean paths (`/login`, `/app/chat`). This requires the server to serve `index.html` for all paths — handled by Vite's dev server and Nginx in production. Hash-based routing would have worked but is less clean.

**`StrictMode` is on in development.** React's StrictMode mounts components twice and may surface issues with effects that run more than once. This is intentional — catching bugs early. The known side effect is `wsManager.run` being invoked twice during dev; `wsManager.close` on the second call handles this cleanly.

## Gotchas

**QueryClient is a module-level singleton.** It is created outside the component tree, so it is shared across HMR reloads in development. If a TanStack Query cache becomes stale during development, a full page refresh (not just HMR) is needed to get a fresh client.
