---
code_file: frontend/src/App.tsx
last_verified: 2026-06-16
stub: false
---

## 2026-06-16 — local-only builds; mode chooser removed

`useAutoRestoreForcedMode` was replaced by `useResolveAppMode`. The new hook
runs inside `ProtectedRoute`, `PublicRoute`, and `RootRedirect` (via
`useResolveAppMode()` calls). Its logic is binary and unconditional:

- `isForcedCloud()` true → `setMode('cloud-web')` — the hosted website.
- Everything else → `setMode('local')` — DMG, `bash run.sh`, dev, and also
  **any stale persisted `cloud-app` value**, which is coerced to `local` on
  the first render after an upgrade.

`ModeSelectPage` was deleted and its `/mode-select` route removed from the
route tree. `ProtectedRoute` and `PublicRoute` previously redirected to
`/mode-select` while `mode` was null; they now render `<PageFallback>` (the
spinner) during the one-tick window before `useResolveAppMode` fills it in.
`RootRedirect` does the same.

The `VITE_FORCE_CLOUD` build-flag branch in `RootRedirect` was dropped; mode
is now resolved via `isForcedCloud()` (reads `window.__NARRANEXUS_CONFIG__`)
in the hook rather than inline during render.

`ModeSelectPage` lazy import: removed.

## 2026-06-16 — inbound entry read pre-render, not from window.location

The bootstrap useEffect no longer calls `takeInboundToken(window.location)`; it
calls `getInboundEntry()` to read the result captured synchronously in
`main.tsx` (`captureInboundEntry`, see [[tokenInbound]] / [[main]]).

Why: the old effect read the URL too late for a **logged-out arena entry**
(`/?source=arena`). React fires effects child-before-parent, and `/` maps to
`RootRedirect` (NOT a ProtectedRoute). When logged out, `RootRedirect`
synchronously renders `<Navigate to="/login">`; that descendant navigation
effect rewrites the URL before App's mount effect runs, so `?source=arena` was
gone before it could be stashed → `isArenaEntry()` found nothing post-login →
no Agent provisioned and no prompt. (Logged-in worked only because
`RootRedirect` waits on the async `checkProviders()` gate before navigating,
leaving a window for the mount effect to read the URL first.) Capturing pre-
render removes the dependency on effect ordering entirely. Token-exchange
logic is unchanged — it just consumes `getInboundEntry()` instead of re-parsing.

## 2026-06-11 — NetMind ?token= inbound bootstrap; /register removed

Added a one-shot bootstrap useEffect: on app init it calls `takeInboundToken(window.location)` (lib/netmindAuth/tokenInbound) — when the page is opened with `?token=<NetMind loginToken>` (a link from netmind.ai or Arena), it strips the token from the URL immediately and exchanges it for our session via api.netmindLogin, then writes configStore (login + setNetmindToken). `?source=` is stashed in sessionStorage('nx-entry-source') for Phase 2 provisioning. Already-logged-in users are skipped. Also removed the RegisterPage lazy import and the `/register` route — cloud sign-up now links out to NetMind's registration page (see LoginPage). RegisterPage.tsx deleted. (Superseded 2026-06-16: see above — the read moved pre-render.)

last_verified: 2026-06-02
stub: false
---

## 2026-06-02 — cloud skips first-login provider setup

`RootRedirect`'s setup gate is now `needsSetup && mode === 'local'` (was
just `needsSetup`). Cloud accounts (`cloud-web` / `cloud-app`) boot on the
system free-tier quota ([[system_provider_service]] / QuotaService), so a
fresh cloud user can chat with zero configuration; the provider screen only
confused users who had no API key to paste. Local installs have no system
provider, so they still must configure one — the gate keeps `/setup` for
`mode === 'local'` only. Cloud users can still add a personal provider later
from Settings (the onboarding checklist + quota panel point them there).

## 2026-05-27 — UpdateBanner mount + updaterStore init

App.tsx now (1) mounts [[UpdateBanner.tsx]] at the root (outside the
router so it surfaces from every page) and (2) calls
`useUpdaterStore.getState().init()` in a useEffect on App mount to
bring the unified auto-updater state mirror online. `teardown()` runs
in the effect cleanup to keep StrictMode happy. Both are no-ops on
web/cloud (the store's `init()` early-returns at `isTauri() === false`,
and the banner only renders on `state.kind === "ready"` which never
happens in browser mode). See [[updaterStore.ts]] for the bridge to
the Rust state machine [[updater.rs]].

## 2026-05-27 — session-expired banner on narranexus:auth-expired

The auth-expired handler now (1) logs out via configStore as before
AND (2) sets `sessionExpired` so a top amber banner explains "Your
session expired. Please sign in again." Auto-dismisses after 12s;
clicking dismisses immediately. Mirrors the `quotaExceeded` banner
pattern at the same site. Why: pre-fix the bounce-to-login was
silent, so cloud users (especially dmg users opening the app a week
after install) saw mysterious errors / abrupt logout with no
explanation. The new wsManager bridge ([[wsManager]]) means WS
AuthError frames also trigger this path, not just REST 401s.

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

**`ProtectedRoute` checks `!mode` before `!isLoggedIn`.** On the very first tick after a logout/wipe, `mode` may be null for one render cycle before `useResolveAppMode` fills it in. Checking `!mode` first ensures the component renders a spinner rather than a `/login` redirect while the base URL is not yet resolved, avoiding a broken login form with no API URL configured.

**Session validation in `ProtectedRoute` is soft.** If `api.getAgents()` throws (backend unreachable), the user is NOT logged out — they stay in the app. Only a `!res.success` response from a reachable backend triggers logout. This prevents local-mode users from being logged out during a backend restart.

**Hard logout on 401 via `narranexus:auth-expired` event.** The `App` component registers a global listener for `narranexus:auth-expired` and calls `configStore.logout()` on receipt. `api.ts` dispatches this event whenever an authenticated request comes back 401 from a non-auth endpoint (see `request<T>` in `lib/api.ts`). This complements `ProtectedRoute`'s one-shot session check: a JWT that expires mid-session — or is invalidated by a backend restart that recycled session state — gets caught by the next API call instead of leaving the UI to spam silent 401s.

**Mode is resolved by `useResolveAppMode`, not inline in `RootRedirect`.** The hook runs as a `useEffect`, which is React-safe. `RootRedirect` (and the route guards) spin a `PageFallback` if mode is still null on the first render, then re-render once the effect fires — typically one tick. The old `VITE_FORCE_CLOUD` inline `setMode` during render has been removed.

**All pages are lazy-loaded.** Every `const Foo = lazy(() => import(...))` call creates a code-split chunk. `Suspense` with `PageFallback` shows a spinner while the chunk loads. The only performance trade-off is a ~100ms delay on first navigation to each page.

**`/app/chat` renders `null` as content.** The chat content (`ChatPanel` etc.) is rendered by `MainLayout`'s child slot logic, not by a dedicated route element. The `<Route path="chat" element={null} />` declaration exists only to make the route valid for `Navigate` destinations.

**`/nm-playground` is a public dev-mode route.** Added in M2 (NM design system Phase 1). Renders `NMPlaygroundPage` — a visual gallery of every NM primitive in light + dark side-by-side. No auth required so it can be loaded before login during visual review. Not linked from any navigation. Should ideally be tree-shaken from production builds, but harmless if left in.

## Gotchas

**`initialize()` is called from `RootRedirect` but is a no-op.** See `runtimeStore.ts` — `initialize` was deprecated and is now an empty function. The call in `RootRedirect` is harmless but should be cleaned up once the need to call it is fully gone from all persisted states.

**`PublicRoute` shows `PageFallback` if `mode=null`.** When localStorage is cleared, mode is null for one tick before `useResolveAppMode` sets it. `PublicRoute` shows a spinner during that window rather than rendering a login form backed by an unresolved API URL. This is expected behavior, not a hang — it resolves on the next render.

**`ProtectedRoute` shows `PageFallback` during session validation.** The `validating` state delays rendering protected content by one async round-trip (`api.getAgents`). On a slow connection this can show the spinner for 1-2 seconds even for logged-in users with valid sessions.

**Arena landing (2026-06-16).** A top-level effect runs `runArenaLandingIfNeeded()` on mount and on the `isLoggedIn` transition (`useConfigStore.subscribe`), so an arrival from arena42.ai (`?source=arena`, stashed in sessionStorage by `takeInboundToken`) provisions/opens the Arena agent — whether the user was already logged in or logs in after landing. A separate effect warms the lazy `MainLayout` chunk on login (`import('@/components/layout/MainLayout')`) so the redirect to `/app/chat` doesn't pay a cold lazy-load (the page-open spinner); `vite.config` `server.warmup` pre-transforms it in dev too.
