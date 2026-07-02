---
code_file: frontend/src/pages/SetupPage.tsx
last_verified: 2026-06-11
stub: false
---
## 2026-06-11 — merge: funnel events wired into the redesigned page

The dev-branch funnel instrumentation and the one-key redesign merged.
`finishSetup(event)` replaces both `goToChat` and dev's `handleDone`:
the footer "Get Started" button (providerCount > 0) and OneKeyOnboard's
`onComplete` fire `setup_completed`; the ghost "Skip for now" button
(providerCount === 0) fires `setup_skipped`; `setup_entered` fires once
on mount behind a StrictMode ref guard. Which event fires depends on
which button the user pressed, never on provider count alone.

## 2026-06-10 (later) — Get Started restored; OneKeyOnboard gained provider picker

Footer is provider-count-aware again: zero providers → ghost "Skip for
now"; any provider → accent "Get Started". Count re-probes when the
Advanced disclosure collapses (the user may have configured providers
inside it). The primary card now covers NetMind/Claude/OpenAI/Yunwu/
OpenRouter via the shared OneKeyOnboard.

## 2026-06-10 — one-key card is the primary first-run surface

SetupPage now renders `OneKeyOnboard` as the main path; the full
`ProviderSettings` moved behind an "Advanced setup" disclosure (collapsed by
default). The provider-count probe + Done/Skip dual-button logic is gone —
success navigates straight to /app/chat via onComplete; "Skip for now"
remains for users with no key.


# SetupPage.tsx — First-time LLM provider configuration wizard

## Why it exists

A new user who has just logged in cannot use the agent without at least one LLM provider configured. Rather than silently dropping them into the chat panel with cryptic errors, `RootRedirect` checks provider count on first load and routes to `/setup` if none are configured. This page is a guided onboarding step that can be skipped (if the user does not yet have API keys).

## Upstream / Downstream

Route: `/setup`, wrapped by `ProtectedRoute`. Entered automatically from `RootRedirect` when `providerCount === 0`, or revisited via direct URL.

On mount: calls `api.getProviders()` (authenticated, identity via auth header) to check current provider count, and fires the `setup_entered` funnel event. Uses the full `ApiClient` — the previous bare `getBaseUrl()` fetch that sent no identity headers was replaced so user identity travels correctly.

Composes `OneKeyOnboard` (primary) and `ProviderSettings` (behind the Advanced disclosure). Every exit path goes through `finishSetup(event)`: fires a funnel event and navigates to `/app/chat`.

## Design decisions

**Funnel instrumentation: fire-and-forget, never blocks navigation.**

Three funnel events are reported from this page via `api.trackFunnelEvent()`:

- `setup_entered` — emitted once on mount via `useEffect([], [])`. Marks that
  the user reached setup.
- `setup_completed` — emitted by `finishSetup` from the footer "Get
  Started" button (shown when `providerCount > 0`) and from
  `OneKeyOnboard`'s `onComplete`.
- `setup_skipped` — emitted by `finishSetup` from the ghost "Skip for
  now" button (shown when `providerCount === 0`).

All three calls use `.catch(() => {})` — the funnel must never block or error
the user's navigation.

**"Skip for now" is visible only when `providerCount === 0`.** If providers are already configured (e.g., user navigated back to `/setup`), there is no skip option — only "Get Started". This prevents showing a skip button to users who have already done the setup.

**Provider count check is best-effort.** If the backend is unreachable, `providerCount` stays 0 (the catch block is silent) so the skip affordance remains. This avoids blocking login when the backend is momentarily unavailable.

**No back button.** Setup is a forward-only flow. To undo provider configuration, the user goes to Settings.

## Gotchas

**`providerCount` is re-probed on mount and when the Advanced disclosure collapses** — the user may have configured providers inside `ProviderSettings`, which flips the footer from "Skip for now" to "Get Started". It does not update live while the disclosure stays open.

**`setup_entered` fires even when the user revisits `/setup` after already configuring providers.** The `useEffect` has no condition on `providerCount`. This is correct — the event tracks "user reached this page", which is true on every visit.
