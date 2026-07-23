---
code_file: frontend/src/components/artifacts/NewTabOmnibox.tsx
last_verified: 2026-07-22
stub: false
---

# NewTabOmnibox.tsx — the "new artifact tab" dialog

## Why it exists

One omnibox covering both ways to create a tab, browser-address-bar style:
type/paste a URL + Enter → opens a URL-tab artifact; type anything else →
live-filters the agent's existing artifacts (including minimized /
other-session) to focus one. One control, two intents, no mode switch for the
user to think about.

## Design decisions

- URL vs search detection is `looksLikeUrl` (in the sibling `urlHeuristics.ts`
  — kept out of this file so the component file only exports a component, per
  the eslint react-refresh rule). Heuristic: has a scheme, or looks like
  host.tld/… ; ambiguous input just filters.
- Picking a minimized artifact restores it; picking a visible one focuses it.
- Opening a URL goes through `artifactStore.openUrl` → `artifactsApi.openUrl`
  → upsert (which auto-focuses the new tab).

## Upstream

Mounted by `ArtifactTabStrip` behind the trailing `+` button (always present,
even with zero artifacts).
