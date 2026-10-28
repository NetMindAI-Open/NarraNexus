---
code_file: frontend/src/components/analytics/WebAnalyticsNotice.tsx
last_verified: 2026-08-13
stub: false
---

# WebAnalyticsNotice.tsx — one-time third-party-analytics disclosure

## Why it exists

The notice half of notice-and-choice consent for the cloud web GTM tag
([[webAnalytics.ts]]), mirroring [[TelemetryNotice.tsx]]. GTM defaults ON for
logged-in cloud users, so a default and its disclosure ship together: this
banner tells the user, once, that the cloud web version sends anonymous
page-interaction data to Google (a third party) and points to Settings → Privacy.

## Gating (must be truthful)

Renders only where GTM is actually active, so it never claims something false.
It re-derives the loader's user-facing gates synchronously (it can NOT use
`isWebAnalyticsLoaded()` — at mount the loader may still be mid-await), and must
stay a COMPLETE mirror of [[webAnalytics.ts]]'s gates:
- Not the Tauri build (`isTauri()`).
- `getWebAnalyticsConfig().gtmId` non-empty (official production host only) —
  both are cheap synchronous gates, run before storage/network.
- User not opted out (`api.getAnalyticsOptOut()`), fail-closed on error.
- Not seen before this browser profile (`localStorage web_analytics_disclosure_seen_v1`,
  fail-closed on storage errors).

Desktop / local / dev / self-host / opted-out therefore never see it. (The one
accepted cost of re-deriving rather than subscribing to the loader: a second
opt-out GET per page load until dismissed. If a fourth gate is ever added to the
loader, add it here too.)

## This file does NOT do

- It does not store or grant consent. localStorage records "the notice was
  SHOWN"; consent lives server-side (the Product-analytics opt-out). Clearing
  the cache re-shows the notice; it cannot re-grant anything.
- It does not load GTM — that is [[webAnalytics.ts]].

## Upstream / downstream

- **Mounted by**: [[MainLayout.tsx]], inside ONE shared bottom-anchored
  `flex-col` slot together with [[TelemetryNotice.tsx]]. This component renders a
  plain `w-full` card (no own `fixed`/offset) — the slot sizes itself, so the two
  cloud disclosures stack and never overlap regardless of copy length. (A prior
  attempt hardcoded `bottom-28` to clear telemetry's height; that was a guess
  that broke as soon as the body wrapped — the shared slot removes the guess.)
- **i18n**: `webAnalyticsNotice.{body,settings,dismiss}` (all 10 locales).

## Related constraints

- Iron rule #10 — keep in sync with the loader's disclosure story.
