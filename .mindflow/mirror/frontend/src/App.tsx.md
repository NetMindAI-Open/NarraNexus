---
code_file: frontend/src/App.tsx
last_verified: 2026-05-18
stub: false
---

## 2026-05-18 — deep-link receiver (Tauri-only)

New `useEffect` listens for `narranexus://install?url=...&sha256=...`
deep links delivered by the Tauri layer:
1. On mount calls `consumePendingDeepLink()` (Tauri IPC) to drain any
   URL the OS handed the process before React was alive.
2. Subscribes to the `deep-link-received` Tauri event for URLs that
   arrive while the app is already running (forwarded into the live
   instance by `tauri-plugin-single-instance`'s deep-link feature).

URLs are parsed; if the host segment is `install`, navigate to
`/app/templates/install` carrying the same query string — that route
points at `BundleImportPage` which detects URL mode and auto-fetches via
`POST /api/bundle/import/from-url`.

Hook is a no-op outside Tauri (web/cloud build), so `isTauri()` guard at
top. Design context:
`drafts/logs/template_sharing_2026_05_18.md`.

# App.tsx — Root routing, route guards, and global side-effects

## Why it exists

The entry point for all React Router routing. Defines the complete route tree, implements `ProtectedRoute` and `PublicRoute` guard wrappers, and owns `RootRedirect` — the logic that decides where to send the user on first load. It also mounts the two global side-effect hooks: `useTheme` (dark mode) and `useTimezoneSync`.

## Upstream / Downstream

Rendered by `main.tsx` inside `BrowserRouter` and `QueryClientProvider`. Lazy-imports all page components via `React.lazy` for route-level code splitting.

Reads from `configStore` (`isLoggedIn`, `userId`, `logout`) and `runtimeStore` (`mode`, `setMode`, `initialize`). On `ProtectedRoute`, validates the session by calling `api.getAgents(userId)` — a live check that the JWT is still accepted.

`RootRedirect` checks provider count via a raw `fetch` to `/api/providers` (not through `api.*`) on every root navigation.

## Design decisions

**`ProtectedRoute` checks `!mode` before `!isLoggedIn`.** When the user clicks "Switch Mode", `mode` and `isLoggedIn` are cleared together in a Zustand batch. React Router's navigation to `/mode-select` is enqueued but has lower priority than the render caused by the store update. Without this ordering, `ProtectedRoute` would see `isLoggedIn=false` and redirect to `/login` (with `mode=null`), landing the user on a broken login page with no API URL configured.

**Session validation in `ProtectedRoute` is soft.** If `api.getAgents()` throws (backend unreachable), the user is NOT logged out — they stay in the app. Only a `!res.success` response from a reachable backend triggers logout. This prevents local-mode users from being logged out during a backend restart.

**Hard logout on 401 via `narranexus:auth-expired` event.** The `App` component registers a global listener for `narranexus:auth-expired` and calls `configStore.logout()` on receipt. `api.ts` dispatches this event whenever an authenticated request comes back 401 from a non-auth endpoint (see `request<T>` in `lib/api.ts`). This complements `ProtectedRoute`'s one-shot session check: a JWT that expires mid-session — or is invalidated by a backend restart that recycled session state — gets caught by the next API call instead of leaving the UI to spam silent 401s.

**`RootRedirect` reads `VITE_FORCE_CLOUD`.** Cloud-web deployments set this env var to skip `ModeSelectPage` entirely. On first render with `mode=null` and `VITE_FORCE_CLOUD=true`, `setMode('cloud-web')` is called inline (not in a `useEffect`), which is a Zustand write during render. This is technically unsafe in React strict mode but is a one-time initialization that only fires when `mode` is null.

**All pages are lazy-loaded.** Every `const Foo = lazy(() => import(...))` call creates a code-split chunk. `Suspense` with `PageFallback` shows a spinner while the chunk loads. The only performance trade-off is a ~100ms delay on first navigation to each page.

**`/app/chat` renders `null` as content.** The chat content (`ChatPanel` etc.) is rendered by `MainLayout`'s child slot logic, not by a dedicated route element. The `<Route path="chat" element={null} />` declaration exists only to make the route valid for `Navigate` destinations.

**`/nm-playground` is a public dev-mode route.** Added in M2 (NM design system Phase 1). Renders `NMPlaygroundPage` — a visual gallery of every NM primitive in light + dark side-by-side. No auth required so it can be loaded before login during visual review. Not linked from any navigation. Should ideally be tree-shaken from production builds, but harmless if left in.

## Gotchas

**`initialize()` is called from `RootRedirect` but is a no-op.** See `runtimeStore.ts` — `initialize` was deprecated and is now an empty function. The call in `RootRedirect` is harmless but should be cleaned up once the need to call it is fully gone from all persisted states.

**`PublicRoute` redirects to `/mode-select` if `mode=null`.** A user who navigates directly to `/login` or `/register` with no stored mode (cleared localStorage) will be bounced to `/mode-select`. This is correct but unexpected if the developer clears storage during testing.

**`ProtectedRoute` shows `PageFallback` during session validation.** The `validating` state delays rendering protected content by one async round-trip (`api.getAgents`). On a slow connection this can show the spinner for 1-2 seconds even for logged-in users with valid sessions.
