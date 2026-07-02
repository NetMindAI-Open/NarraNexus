---
code_file: frontend/src/pages/LoginPage.tsx
last_verified: 2026-06-16
stub: false
---

## 2026-06-16 — "Change mode" button removed; `cloud-app` mode gone

The "Change Mode" / `handleChangeMode` button and its handler were removed from
the login form. There are now only two modes (`local` | `cloud-web`); mode is
resolved automatically by `useResolveAppMode` in App.tsx — no user-facing
chooser exists. `isCloudMode` is `mode === 'cloud-web'` (was previously
`mode === 'cloud-web' || mode === 'cloud-app'` to cover the removed
`cloud-app` variant).

The conditional `"Change mode" button is hidden for cloud-web` in the Design
decisions section no longer applies — the button is absent entirely.

## 2026-06-12 — "Forgot password?" entry + account-migration notice

Cloud branch gained a "Forgot password?" link under the password field
(`showForgot` state) that opens [[ForgotPasswordCard.tsx]], rendered next to the
CreateUser / AuthBind dialogs. Also added a static notice banner at the top of
the cloud form telling legacy users their data was migrated to the account
under their invite-code email and that they must reset their password to sign
in, with a support contact (bin.liang@netmind.ai). Local branch untouched.

## 2026-06-11 — cloud branch → NetMind login

Cloud branch replaced: email + password login via `useNetmindAuth.emailLogin`, three OAuth buttons (Google, Microsoft, GitHub) via `useNetmindAuth.startOAuth`, bind dialog via `AuthBindDialog` when `netmind.bindInfo` is set, and Sign-up as an external `<a href={getNetmindConfig().registerUrl}>` link. `api.login` is no longer called in cloud mode. Local branch untouched.

# LoginPage.tsx — Dual-mode login (local user_id / cloud NetMind email+OAuth)

## Why it exists

The login experience differs based on deployment mode. Local mode has no password — just a user_id that acts as a local identity. Cloud mode uses NetMind.AI authentication: email + password login, or OAuth (Google, Microsoft, GitHub). A single component handles both variants by reading `runtimeStore.mode` and rendering entirely different form subtrees.

## Upstream / Downstream

Route: `/login`, wrapped by `PublicRoute` in `App.tsx` (redirects to `/` if already logged in).

Reads `mode` from `runtimeStore` to determine which branch to render.

**Cloud branch**: uses `useNetmindAuth` hook for all auth actions. On success (via `onSuccess` callback): calls `configStore.login(userId, token, role, {displayName, email})`, `configStore.setNetmindToken(loginToken)`, fetches agents, then navigates. OAuth flows open a popup that sends a `postMessage`; `useNetmindAuth` handles the callback internally. When OAuth returns a `bindInfo`, renders `AuthBindDialog` as a modal. Sign-up link is an external `<a>` pointing to `getNetmindConfig().registerUrl`. `api.login` is NOT called in cloud mode.

**Local branch**: unchanged. Calls `api.login(userId)` directly, then `configStore.login`, then agents. Renders `CreateUserDialog` modal for the "Create New User" flow.

## Design decisions

**Token stored before `getAgents` call (cloud).** The sequence in `onSuccess`: `login(userId, token)` (which triggers Zustand persist → localStorage) → `api.getAgents()` (which reads the token from localStorage via `getAuthHeaders`). Same reasoning as before — see commit `b4b58ce`.

**Cloud branch is an entirely separate JSX subtree.** Rather than conditional rendering of individual fields, the two modes render completely separate `<div className="space-y-5 mt-6">` trees. This makes each branch independently readable and avoids shared state leaking between modes.

**`handleLocalLogin` replaces the old `handleLogin`.** The cloud login path is now fully owned by `useNetmindAuth`; the old unified `handleLogin` was split to avoid dead code in cloud mode. `handleLocalLogin` is only reachable in local mode.

**No mode-switch UI.** Mode is resolved automatically by `useResolveAppMode` (App.tsx); the login page has no button for switching modes. Users on the hosted website always see the cloud-web (NetMind) form; users on any local build always see the local (user_id) form.

**Post-login `?next=` return path (open-redirect guarded).** `ProtectedRoute` sends unauthenticated visitors to `/login?next=<encoded-path>`; after auth, login reads `next` from `location.search` and navigates there via `navigate(isSafeReturnTo(next) ? next : '/')`. The guard (`lib/safe-return`) accepts only same-origin relative paths, so a crafted `?next=https://evil.com` falls through to `/` instead of redirecting off-site.

## Gotchas

**`PublicRoute` shows a spinner if `mode` is null.** If localStorage is cleared (e.g., DevTools), the page briefly shows `PageFallback` on the first tick before `useResolveAppMode` sets mode to `local`. This resolves immediately and is not a regression from the old `/mode-select` redirect.

**`CreateUserDialog` auto-fills the login field via `onCreated(userId)`.** After successful user creation, the dialog calls `onCreated` which sets the `userId` state in `LoginPage`. The user still needs to click "Access Terminal" manually — there is no auto-submit.
