---
code_file: frontend/src/__tests__/zh-localization.test.ts
last_verified: 2026-07-21
stub: false
---

# zh-localization.test.ts — localization boundary regression

## Why it exists

First-run and model configuration surfaces accumulated English literals even
though the application already had an active Chinese locale. This test keeps
the affected UI behind translation keys and verifies that Chinese values do
not silently fall back to the English source strings.

## Design decisions

The test checks the locale dictionaries and the affected source boundaries
directly. Rendering these components would require unrelated router, store,
and API setup; source assertions isolate the actual regression: user-visible
copy bypassing i18n. It covers the settings shell, one-key/provider setup, and
the whole model configuration surface rather than a single reasoning label,
and verifies that a persisted generic English bootstrap greeting is localized
again when history is rendered. The same boundary covers ChatPanel's live
execution statuses, preventing `Loading context…` and adjacent startup phases
from bypassing i18n. It also locks the persistent chat security reminder behind
`chat.securityReminder`, preventing the warning from remaining English in the
Chinese interface.

## Gotchas

When a key is renamed, update both locale files and the corresponding source
assertion. The test intentionally covers only the onboarding, setup, bootstrap
greeting, live execution status, and model configuration surfaces named by
this localization fix.
