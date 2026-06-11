---
code_file: frontend/src/pages/SetupPage.tsx
last_verified: 2026-06-09
stub: false
---

# SetupPage.tsx — First-time LLM provider configuration wizard

## Why it exists

A new user who has just logged in cannot use the agent without at least one LLM provider configured. Rather than silently dropping them into the chat panel with cryptic errors, `RootRedirect` checks provider count on first load and routes to `/setup` if none are configured. This page is a guided onboarding step that can be skipped (if the user does not yet have API keys).

## Upstream / Downstream

Route: `/setup`, wrapped by `ProtectedRoute`. Entered automatically from `RootRedirect` when `providerCount === 0`, or revisited via direct URL.

On mount: calls `api.getProviders()` (authenticated, identity via auth header) to check current provider count, and fires the `setup_entered` funnel event. Uses the full `ApiClient` — the previous bare `getBaseUrl()` fetch that sent no identity headers was replaced so user identity travels correctly.

Composes `ProviderSettings` component. On "Done" or "Get Started": fires a funnel event and navigates to `/app/chat`.

## Design decisions

**Funnel instrumentation: fire-and-forget, never blocks navigation.**

Three funnel events are reported from this page via `api.trackFunnelEvent()`:

- `setup_entered` — emitted once on mount via `useEffect([], [])`. Marks that
  the user reached setup.
- `setup_completed` — emitted in `handleDone` when `providerCount > 0`. The
  user configured at least one provider.
- `setup_skipped` — emitted in `handleDone` when `providerCount === 0`. Both
  the explicit "Skip for now" ghost button and the "Done" button with zero
  providers take the same `handleDone` path, so both count as a skip.

All three calls use `.catch(() => {})` — the funnel must never block or error
the user's navigation.

**"Skip for now" is visible only when `providerCount === 0`.** If providers are already configured (e.g., user navigated back to `/setup`), there is no skip option — only "Get Started". This prevents showing a skip button to users who have already done the setup.

**Provider count check is best-effort.** If the backend is unreachable, `loaded` is set to `true` with `providerCount` staying 0 (the catch block is silent). The user is sent to `/app/chat` on Done. This avoids blocking login when the backend is momentarily unavailable.

**No back button.** Setup is a forward-only flow. To undo provider configuration, the user goes to Settings.

## Gotchas

**`providerCount` state is local and not reactively updated.** If the user adds a provider via `ProviderSettings` and the count changes, `providerCount` does not update because the check only runs on mount. The button text changes from "Done" to "Get Started" only on re-mount. This is acceptable — the user is expected to click "Get Started" after configuring, triggering a navigation anyway.

**`setup_entered` fires even when the user revisits `/setup` after already configuring providers.** The `useEffect` has no condition on `providerCount`. This is correct — the event tracks "user reached this page", which is true on every visit.
