---
code_file: frontend/src/lib/chatDays.ts
last_verified: 2026-07-23
stub: false
---

# chatDays.ts — chat day-separator helpers

`chatDayInfo(ts, now?)` classifies a timestamp into its LOCAL calendar
day: stable `key` for grouping, `kind` (today / yesterday / date) so the
caller i18n's the relative labels, and a locale-formatted `label` (year
included only when it differs from the current year). `now` is
injectable for tests. Consumer: [[ChatPanel.tsx]] timeline separators.
Tests: `__tests__/chatDays.test.ts`.
