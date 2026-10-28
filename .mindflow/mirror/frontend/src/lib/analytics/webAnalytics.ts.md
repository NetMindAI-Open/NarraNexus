---
code_file: frontend/src/lib/analytics/webAnalytics.ts
last_verified: 2026-08-13
stub: false
---

# webAnalytics.ts — consent-gated Google Tag Manager loader (web only)

## Why this file exists

GTM must NOT be an inline `<script>` in `index.html`. An inline tag loads
unconditionally; loading from code lets `initWebAnalytics()` enforce gates that
an inline tag cannot. Three gates, all must pass:

1. **Consent — single source of truth.** Reads `api.getAnalyticsOptOut()` (same
   backend flag the kernel product-analytics sink honours) and injects only for
   an opted-in user. Fail-closed on any error. `App.tsx` calls this on
   `isLoggedIn` because the opt-out is per-user.
2. **Desktop safety.** `isTauri()` short-circuits (`csp:null` +
   `withGlobalTauri:true` webview).
3. **Host + config gate.** `getWebAnalyticsConfig()` returns a non-empty id only
   on the official production host, or when a deploy/VITE value is set.

## Account-switch / mid-session opt-out (the subtle part)

A `<script>` tag, once injected, cannot be un-loaded. So consent is enforced by
**reloading** at the two transitions where a still-running GTM would otherwise
track a now-opted-out user:

- **Same-tab account switch**: if `initWebAnalytics()` runs for an opted-out
  user while `started` is already true (GTM loaded for a previous opted-in user
  in this tab), it calls `window.location.reload()` to shed GTM.
- **Mid-session toggle off**: [[PrivacySettings.tsx]] `toggleAnalytics` (the
  REACHABLE panel — the old `SettingsModal` never mounts) reloads after
  `setAnalyticsOptOut(true)` succeeds — but ONLY when `isWebAnalyticsLoaded()`
  (the exported view of `started`) is true, so a mode where GTM never loaded
  (desktop / local / dev / self-host) never eats a pointless reload. It also
  calls `markWebAnalyticsConsentRevoked()` first: that sets the module-level
  `consentRevoked`, so an `initWebAnalytics()` that is mid-await (it read
  opt-out=false a moment ago) still bails instead of injecting after the toggle
  flipped. `consentRevoked` resets on the next page load — which is exactly the
  reload this path triggers.

This deliberately does NOT touch the shared `configStore.logout()` — a reload
there would wipe the session-expired banner (see [[App.tsx]] 2026-08-06 entries)
before it renders. Enforcing at the consent transitions keeps the blast radius
small.

Disclosure: because this toggle's copy (`analyticsDesc`, all 10 locales) now
names GTM as a third-party service, and a one-time [[WebAnalyticsNotice.tsx]]
banner discloses it on the official host, the toggle is honest consent for both
the first-party and the GTM data flows.

## This file does NOT do

- It does not load Microsoft Clarity or any session-replay tool. In a
  conversation product, replay captures chat content, contradicting the
  localized "never collect conversation content" promise. GTM is event-only.
- It does not define events/tags — those live in the GTM console (protagolabs).
- It is NOT the kernel analytics seam
  ([[__init__.py]] under `narranexus/platform/analytics`), which is first-party,
  DB-only, no vendor. Two independent systems. First-party product events go
  through [[productAnalytics.ts]]; new client-side analytics files belong in
  this `lib/analytics/` subpackage.

## Security note (write access = arbitrary JS on prod)

Write access to the GTM container equals arbitrary JavaScript execution on the
production origin (agent.narra.nexus) — a GTM Custom HTML tag is arbitrary JS,
deployed by editing the console with no repo change or release. That origin
holds the user's session credentials, so GTM console membership must be managed
as production credentials: minimal members, two-person approval for changes.
This is an inherent property of choosing GTM (a settled product decision), not a
defect of this loader.

## Known consequence (deliberate)

Loading waits for the per-user opt-out, so anonymous / logged-out visitors are
not tracked (no login/landing acquisition attribution). Accepted vs. GTM
Consent Mode; revisit with Consent Mode if anonymous tracking is needed.

## Gotcha / edge cases

- **`started` is module-level** and only resets on a full page load — that is
  what makes the reload path work. Tests must `vi.resetModules()` + re-import
  per case, or the first successful inject makes later cases silently pass.
- **Fail-closed on opt-out error** means an unauthenticated / offline call
  loads nothing; the account-switch reload only fires when the opt-out lookup
  succeeds and returns true.

## Related constraints

- Iron rule #10 — update this mirror in the same commit as any behavioural
  change.
- Iron rule #7 — `isTauri()` keeps desktop analytics-free, so `bash run.sh` /
  DMG behave the same (both load nothing).
