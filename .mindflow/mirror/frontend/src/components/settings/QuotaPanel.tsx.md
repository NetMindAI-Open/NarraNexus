---
code_file: frontend/src/components/settings/QuotaPanel.tsx
stub: false
last_verified: 2026-04-16
---

# Intent

Compact read-only panel that surfaces the signed-in user's system
free-tier token quota above the provider configuration section.
Deliberately renders nothing (returns null) when:

1. The app is running in local mode (`useRuntimeStore.mode !== 'cloud'`)
2. The backend reports the feature disabled (`{enabled: false}`)
3. The quota row exists but status is `uninitialized` is still rendered,
   but as a plain text hint — no progress bars.

So local users and cloud users whose feature is off never see any
quota-related UI — no layout shift, no "feature off" copy.

## Why mounted in ProviderSettings

The user's mental model: "This is where I configure my LLM access."
Placing the free-tier budget above the provider list reinforces
"Here's what you have; here's how to go beyond it." The panel is
also inside the Settings modal, which is where users go when things
run out.

## Upstream
- `api.getMyQuota()` — single GET on mount. No polling; the panel is
  a snapshot, not a live meter (users don't burn tokens fast enough
  to notice staleness in the settings modal).

## Downstream
- `useRuntimeStore` — local vs cloud mode check
- Tailwind / CSS variables of the bioluminescent terminal design system

## Gotchas
- We wait for `loaded` before deciding to render null vs content so
  the component doesn't flash visible-then-gone on mount.
- The `exhausted` state recolours bars to `--accent-error` and shows
  a hint to configure the user's own provider. Hint is positional
  rather than a modal — staff don't want to intercept users mid-flow.
- Progress bar math uses `max(0, min(100, ...))` via `pct()` so
  post-concurrent-overdraw states (used slightly > total) still look
  sensible.
