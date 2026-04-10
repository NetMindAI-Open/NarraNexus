---
code_file: frontend/src/pages/LoginPage.tsx
last_verified: 2026-04-10
stub: false
---

# LoginPage.tsx — Dual-mode login (local user_id / cloud user_id+password)

## Why it exists

The login experience differs based on deployment mode. Local mode has no password — just a user_id that acts as a local identity. Cloud mode requires both a user_id and password, and returns a JWT token. A single component handles both variants by reading `runtimeStore.mode` and conditionally rendering the password field.

## Upstream / Downstream

Route: `/login`, wrapped by `PublicRoute` in `App.tsx` (redirects to `/` if already logged in).

Reads `mode` from `runtimeStore` to determine whether cloud fields and the "Change Mode" button are shown. On submit: calls `api.login(userId, password?)`, then immediately calls `login()` on `configStore` (storing the JWT), then fetches and stores agents via `api.getAgents` + `configStore.setAgents/setAgentId`. Navigates to `/` on success (which routes through `RootRedirect` to `/setup` or `/app/chat`).

Renders `CreateUserDialog` as a modal for the local-mode "Create New User" flow. In cloud mode renders a "Create Account" button that navigates to `/register`.

## Design decisions

**Token stored before `getAgents` call.** Commit `b4b58ce` fixed a bug where `getAgents` returned 401 in cloud mode because the token was not in localStorage yet. The sequence is: `login(userId, token)` (which triggers Zustand persist → localStorage) → `api.getAgents()` (which reads the token from localStorage via `getAuthHeaders`).

**"Change Mode" button is hidden for `cloud-web` mode.** Force-deployed cloud builds (where the frontend and backend share an origin, set via `VITE_FORCE_CLOUD`) should not offer users a way to switch to local mode. The button is only shown when `mode !== 'cloud-web'`.

**`handleChangeMode` clears `cloudApiUrl` before resetting mode.** Clearing the URL prevents the next cloud mode selection from silently reusing the old server URL without prompting the user.

## Gotchas

**`PublicRoute` redirects to `/mode-select` if `mode` is null.** If a user's localStorage was cleared (e.g., via DevTools or a `localStorage.clear()` call in Tauri mode-switch logic), they will be redirected from `/login` to `/mode-select` even if they navigate directly. This is correct behavior but can be surprising during testing.

**`CreateUserDialog` auto-fills the login field via `onCreated(userId)`.** After successful user creation, the dialog calls `onCreated` which sets the `userId` state in `LoginPage`. The user still needs to click "Access Terminal" manually — there is no auto-submit.
