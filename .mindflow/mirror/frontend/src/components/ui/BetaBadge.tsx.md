---
code_file: frontend/src/components/ui/BetaBadge.tsx
last_verified: 2026-07-28
stub: false
---

# BetaBadge.tsx — Brand "Beta" marker next to the NarraNexus logo

## Why it exists

The product is publicly labeled beta to manage user expectations. One shared
component keeps the marker identical at every brand touchpoint — the sidebar
header, the login page, and the setup page — instead of three hand-rolled
copies drifting apart.

## How it works / design

- Composes [[Badge.tsx]] (`size="sm"` — the same 9px mono scale as the footer
  version chip) inside the Radix [[tooltip.tsx]] with its own local
  `TooltipProvider` (the app has no global provider; ProviderSettings and
  BookmarkStrip set the same precedent).
- **The "Beta" label is a deliberate untranslated literal.** Industry
  convention keeps "Beta" in Latin script in every locale (it is part of the
  brand lockup, like the logo itself). Only the hover note is translated:
  `common.betaTooltip`, present in all 10 locale files.
- The trigger carries `aria-label={note}` so the expectation-setting note is
  reachable for screen readers and on touch devices, where Radix hover-only
  content never mounts.

## Upstream / Downstream

Used by [[Sidebar.tsx]] (expanded header), [[LoginPage.tsx]] (brand header),
[[SetupPage.tsx]] (header). Exported from the ui barrel ([[index.ts]]).
Pinned by [[BetaBadge.test.tsx]].

## Gotchas

When the beta period ends, delete the component, its three usages, the
`common.betaTooltip` key in the 10 locale files, and this mirror — there is
no feature flag; presence in the tree IS the flag.
