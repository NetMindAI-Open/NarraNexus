---
code_file: frontend/src/pages/__tests__/SettingsPage.nav.test.tsx
last_verified: 2026-07-21
stub: false
---

# SettingsPage.nav.test.tsx — settings navigation boundary

## Why it exists

The settings navigation has per-user visibility rules: Account & Subscription
is available only when the session has a NetMind token. The test isolates that
filter from heavy settings panels and network calls.

## Design decisions

The i18n mock has a stable translator identity and resolves the navigation keys
used by assertions to English test copy. This lets the test verify accessible
button names after the page moved from hardcoded labels to locale keys without
making the test depend on the full i18next runtime.

## Gotchas

When a navigation label key changes, update the small test translation map as
well as both production locale dictionaries.
