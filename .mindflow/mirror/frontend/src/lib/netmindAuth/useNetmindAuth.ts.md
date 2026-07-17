---
code_file: frontend/src/lib/netmindAuth/useNetmindAuth.ts
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — desktop OAuth path (Tauri bridge + poll)

`startOAuth` branches on `isTauri()`: browser keeps `window.open` + the `message`
listener; desktop calls `openNetmindOAuth(url)` ([[tauri.ts]] →
[[netmind_oauth.rs]]) then **polls** `takeNetmindOAuthResult` every 800ms
(≤3 min) for the bridged `{code,state}`, decodes via `decodeOAuthPayload`
(handles both URI-encoded and plain JSON), and resumes `handleAuthCallback`
through `handleAuthCallbackRef` (kept current since startOAuth is defined before
handleAuthCallback). Poll interval tracked in `oauthPollRef` (cleared on second
click + on unmount). **Why poll, not a Tauri event**: the first DMG test showed
the OAuth child window closing (Rust bridge fired) but the app staying on the
login page — a live `listen` needs `window.__TAURI__` (withGlobalTauri is off
here) and silently no-ops; `invoke`-based polling always works.
`sessionStorage['nm-oauth-type']` is still set in the main window, so
handleAuthCallback reads it unchanged. Only relevant once local dual-mode Power
login is enabled (else no OAuth buttons show).

## 2026-06-12 — forgot-password methods (sendResetCode + resetPassword)

Two methods added for NetMind's own password reset (cloud passwords ARE NetMind
passwords, so no backend): `sendResetCode(email)` → POST `/register/sendCode`
with `type: 2` (forgot-password path; no reCAPTCHA there — NOTE: send-code lives
under `/register/`, only the reset itself is under `/user/`), and
`resetPassword(email, code, newPassword)` → POST `/user/resetPassword`. Both
call NetMind directly via `netmindPost` like emailLogin, share the hook's
loading/error state, and return a boolean so the UI ([[ForgotPasswordCard.tsx]])
can advance step / close. Exposed on the hook's return.

# useNetmindAuth.ts — NetMind login orchestration hook

## Why it exists

The cloud login page needs to support three distinct entry points — email/password,
OAuth popup (Google/Microsoft/GitHub), and bandType binding (when a third-party
OAuth account needs to be linked). All three paths ultimately produce a NetMind
`loginToken` that must be exchanged for our own JWT via `api.netmindLogin`. This
hook centralises that convergence so the login page component does not need to
know about the internal choreography.

Without this hook, each entry path would independently manage loading/error state,
call `netmindPost` with different endpoint shapes, and then duplicate the
`api.netmindLogin` exchange call — a maintenance problem as soon as any of the
three paths evolves.

## What this file does NOT do

It does not render anything — it is a pure React hook. It does not perform
reCAPTCHA validation; `ckType=2` in the email login body tells NetMind to skip
it. It does not persist the resulting JWT — that is the caller's responsibility
(the `onSuccess` callback receives both the backend response and the raw
`loginToken` so the caller can store what it needs for Phase 2/3). It does not
open or manage the OAuth popup window beyond calling `window.open`; the popup
communicates back via `window.postMessage`.

## Upstream / downstream

- **Used by**: the cloud login page component (not yet created), which renders
  email/password fields, OAuth buttons, and optionally a bind confirmation dialog.
  The component wires `onSuccess` to update auth context / redirect.
- **Depends on**:
  - `./request` (`netmindPost`) for all calls to the NetMind auth API
  - `./constants` (`baseRequestParams`) to inject the required boilerplate fields
  - `./crypto` (`encryptPassword`, `generateRandomString`) for DES-encrypting the
    password before sending
  - `./types` (`AuthBindInfo`, `NetmindUser`) for the bind-flow state shape
  - `@/lib/api` (`api.netmindLogin`) to exchange the NetMind loginToken for our JWT
  - `@/lib/runtimeConfig` (`getNetmindConfig`) to obtain `accountsUrl` and `authApi`
    for building the OAuth popup URL
  - `@/types/api` (`NetmindLoginResponse`) as the type of the `onSuccess` first arg

## OAuth popup flow

`startOAuth(type)` opens a popup window pointing to NetMind's `auth.html`. The
popup completes OAuth and calls `window.opener.postMessage({type:'auth', code, state})`.
The hook registers a `message` event listener on mount (cleaned up on unmount)
that intercepts this message and calls `handleAuthCallback(code, state)`. Callers
can also call `handleAuthCallback` directly (e.g. from a redirect-based OAuth flow
where the callback lands on a dedicated route).

## Bind flow

When `handleAuthCallback` gets a response without `loginToken`, it means NetMind
needs the user to confirm or supply an email before completing the link. The hook
surfaces this as `bindInfo` state. The login page renders a bind dialog; the user
submits, triggering `submitBind`. After a successful bind, `bindInfo` is cleared
and the `loginToken` exchange proceeds normally.

## Design decisions

- **`onSuccess` receives both `res` and `loginToken`**: the caller needs the raw
  `loginToken` to stash for Phase 2 (credits) and Phase 3 (API key generation).
  Passing only the backend response would require a second round-trip or an
  out-of-band store.
- **`ckType: 2` — no reCAPTCHA**: NetMind supports multiple check types; type 2
  skips the captcha challenge. This is intentional: NarraNexus runs behind its own
  login gate and does not need captcha on top.
- **`sessionStorage` for OAuth type**: the popup opens in a separate browsing
  context; the easiest way to remember which OAuth provider was selected so
  `userCallBack` receives the correct `oauthType` is to write it before opening the
  popup and read it back in the callback. `localStorage` would persist across tabs
  undesirably; `sessionStorage` is scoped to the tab.

## Gotcha / boundary cases

- **Trigger**: calling `startOAuth` in a test environment (or a strict popup
  blocker) without mocking `window.open`.
  **Symptom**: the test either throws on `window.open` being undefined or the popup
  is silently blocked.
  **Root cause**: `window.open` is a browser primitive; tests need to `vi.spyOn`
  it. The hook does not guard against a blocked popup — the message listener simply
  never fires.

- **Trigger**: the `message` event fires from a cross-origin source (e.g. an iframe
  or unrelated popup) that happens to include `{type:'auth', code, state}`.
  **Symptom**: `handleAuthCallback` is invoked with attacker-controlled `code` and
  `state`.
  **Root cause**: there is no `e.origin` check in the listener. A future hardening
  step should validate `e.origin === getNetmindConfig().accountsUrl`.

- **Trigger**: `netmindPost` is called but `getNetmindConfig().authApi` is `""` (no
  runtime config in dev).
  **Symptom**: the request hits a relative path on the NarraNexus backend, gets a
  404, and the error surfaces as `"Login failed"`.
  **Root cause**: inherited from `request.ts` — see its Gotcha for details.

## New-developer traps

- The hook only exports named functions; it has no default export. Import as
  `import { useNetmindAuth } from './useNetmindAuth'`.
- `submitBind` is a no-op if `bindInfo` is `null`. Don't call it before
  `handleAuthCallback` has set the bind info.
- `closeBind` clears `bindInfo` without triggering any backend call — it is purely
  a UI cancel action.

## Related constraints

- Iron Law #3 (module independence) — this hook must not import from other
  netmindAuth modules except the four peer files (`request`, `constants`, `crypto`,
  `types`) which are its direct and intentional dependencies.
- See `references/phase1-frontend-login-migration.md` for the full Phase 1 login
  migration design, including the three-path convergence rationale and the
  `loginToken` stashing contract for Phases 2 and 3.
