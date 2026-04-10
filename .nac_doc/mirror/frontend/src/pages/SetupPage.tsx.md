---
code_file: frontend/src/pages/SetupPage.tsx
last_verified: 2026-04-10
stub: false
---

# SetupPage.tsx — First-time LLM provider configuration wizard

## Why it exists

A new user who has just logged in cannot use the agent without at least one LLM provider configured. Rather than silently dropping them into the chat panel with cryptic errors, `RootRedirect` checks provider count on first load and routes to `/setup` if none are configured. This page is a guided onboarding step that can be skipped (if the user does not yet have API keys).

## Upstream / Downstream

Route: `/setup`, wrapped by `ProtectedRoute`. Entered automatically from `RootRedirect` when `providerCount === 0`, or revisited via direct URL.

On mount: fetches `GET /api/providers?user_id=...` to check current provider count. Uses `getBaseUrl()` (re-exported from `api.ts`) directly rather than an `api.*` wrapper method, since there is no typed wrapper for the provider list endpoint in `api.ts`.

Composes `ProviderSettings` component. On "Done" or "Get Started": navigates to `/app/chat`.

## Design decisions

**"Skip for now" is visible only when `providerCount === 0`.** If providers are already configured (e.g., user navigated back to `/setup`), there is no skip option — only "Get Started". This prevents showing a skip button to users who have already done the setup.

**Provider count check is best-effort.** If the backend is unreachable, `needsSetup` defaults to `false` and the user is sent directly to `/app/chat`. This avoids blocking login when the backend is momentarily unavailable.

**No back button.** Setup is a forward-only flow. To undo provider configuration, the user goes to Settings.

## Gotchas

**`getBaseUrl()` is the old re-export from `api.ts`, equivalent to `getApiBaseUrl()` from `runtimeStore`.** They point to the same function. This inconsistency in the codebase is harmless but may confuse newcomers into thinking there are two separate URL resolution paths.

**`providerCount` state is local and not reactively updated.** If the user adds a provider via `ProviderSettings` and the count changes, `providerCount` does not update because the check only runs on mount. The button text changes from "Done" to "Get Started" only on re-mount. This is acceptable — the user is expected to click "Get Started" after configuring, triggering a navigation anyway.
