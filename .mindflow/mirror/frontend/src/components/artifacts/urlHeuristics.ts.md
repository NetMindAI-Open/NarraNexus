---
code_file: frontend/src/components/artifacts/urlHeuristics.ts
last_verified: 2026-07-22
stub: false
---

# urlHeuristics.ts — URL-vs-search detection for the new-tab omnibox

## Why it exists

The omnibox ([[NewTabOmnibox.tsx]]) decides, per keystroke, whether the user is
typing a URL to open or a query to filter existing artifacts. That decision is
these two pure functions. They live in their own file (not in the component)
so the component file only exports a component — the eslint `react-refresh/
only-export-components` rule forbids mixing.

## Contract

- `looksLikeUrl(text)` — true for `https?://…`, or a bare `host.tld[/path]`
  (requires a dot + TLD-ish tail); false for anything with whitespace or a
  single dotless word (that is a search query). Deliberately conservative:
  ambiguous input falls through to search, which is the cheap wrong-guess.
- `normalizeUrl(text)` — prepends `https://` to a bare host, leaves an explicit
  scheme untouched, trims whitespace.

## Tested by

`__tests__/urlHeuristics.test.ts` — exhaustive URL/query cases.
