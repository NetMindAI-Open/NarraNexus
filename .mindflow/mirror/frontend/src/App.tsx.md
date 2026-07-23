---
code_file: frontend/src/App.tsx
last_verified: 2026-07-21
stub: false
---

## 2026-07-20 (续) — 横幅"退出重登"改为"Settings → Account 里接入"

use-subscription 按钮接上后（[[NetmindAccountPanel]] 同日条目），横幅里
"then sign out and back in to link it"的笨拙引导改为指向面板的 Link it now
按钮所在位置。#124 自己就说文案是止血不是终态——终态到了。
## 2026-07-21 — /app/marketplace 路由

新增 lazy MarketplacePage 路由,与其他页面同模式。


## 2026-07-20 — quota-exceeded 横幅文案补「订阅 NetMind.AI 套餐」

与 [[provider_resolver]] / [[llm_failure]] 同批：额度耗尽的用户现在可能已经
被自动绑上了一把没余额的 NetMind key，只提示"添加自己的 API key"对他们无解。

遗留问题（本次未处理，超出改动范围）：**这条横幅是全应用少数没走 i18n 的
用户可见文案** —— App.tsx 至今没有引入 `useTranslation`，为一条字符串把 hook
引进应用外壳风险大于收益。若将来 App.tsx 因别的原因接入 i18n，顺手把它一起收了。

## 2026-07-13 — Agent 实时层熔断器接入

监听 `narranexus:agent-circuit-open`，渲染红色顶部横幅：paused 时带『Resume agent』按钮（调 `api.resetAgentCircuitBreaker` 清暂停），cooling 只提示稍后重试。mirror quota/auth 横幅模式。


## 2026-07-07 — free-tier auto-switch one-time banner (#48)

When the free tier runs out and the backend auto-switches the user to their own
provider, it writes a one-time SYSTEM notice tagged `source.type
"free_tier_switch"`. New effect polls `api.getNotices(true)` on mount + window
`focus` (the switch happens mid-session, server-side, on a request that
otherwise succeeds — no error for api.ts to catch), and on a hit shows a green
dismissible top banner then `markNoticeRead` so it surfaces exactly once. Mirrors
the existing `quota-exceeded` / `session-expired` banner pattern (hard-coded
English copy, click-to-dismiss).

## 2026-06-23 — `/app/you` route added

Added a lazy `/app/you` → [[YouWorkspace]] route under the `/app` (MainLayout)
group. Like `dashboard` / `system`, it is a sub-page rendered through the
`<Outlet>` overlay (so `isSubPage` in [[MainLayout]] is true and it gets the
close-X). Entered from the sidebar user avatar ([[Sidebar]]).

## 2026-06-11 — NetMind ?token= inbound bootstrap; /register removed

Added a one-shot bootstrap useEffect: on app init it calls `takeInboundToken(window.location)` (lib/netmindAuth/tokenInbound) — when the page is opened with `?token=<NetMind loginToken>` (a link from netmind.ai or Arena), it strips the token from the URL immediately and exchanges it for our session via api.netmindLogin, then writes configStore (login + setNetmindToken). `?source=` is stashed in sessionStorage('nx-entry-source') for Phase 2 provisioning. Already-logged-in users are skipped. Also removed the RegisterPage lazy import and the `/register` route — cloud sign-up now links out to NetMind's registration page (see LoginPage). RegisterPage.tsx deleted.

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
