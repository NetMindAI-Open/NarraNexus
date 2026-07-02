---
code_file: frontend/src/components/settings/ProviderSummaryCard.tsx
last_verified: 2026-06-10
stub: false
---
# ProviderSummaryCard.tsx — at-a-glance LLM wiring summary

## Why it exists

The Settings → Providers section used to lead with the full 1400-line
ProviderSettings. After the 2026-06-10 one-key onboarding work, Settings
adopts the same logic as first-run /setup: a simple face first, the full
UI behind an "Advanced configuration" disclosure. This card IS that
simple face for users who already have providers: three read-only rows —
Agent (framework · model · provider), Helper (model · provider), Keys
(active providers with masked keys).

## Upstream / downstream

- **Rendered by**: `pages/SettingsPage.tsx` `ProvidersSection` (only
  when providerCount > 0; zero-provider users get OneKeyOnboard
  instead).
- **Reads**: `api.getProviders()` (providers + slots) and
  `api.getAgentFramework()` (framework label), fetched in parallel.
- **refreshToken prop**: parent bumps it when the Advanced disclosure
  closes or onboard completes, so the summary reflects edits made in
  the full UI.

## Design decisions

- **Strictly read-only** — every edit affordance stays in Advanced
  (ProviderSettings). No quick-switchers here; that would re-grow the
  complexity this card exists to hide.
- Backend-unready states render nothing (null) rather than a broken
  card; the section header + disclosure still show.
- NOTE: `api.getAgentFramework` runs an auth probe server-side
  (subprocess, ~100ms-10s worst case) — acceptable for a settings page
  load; if it ever becomes a drag, add a probe-free variant.
