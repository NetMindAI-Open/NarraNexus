---
code_file: frontend/src/components/onboarding/GuideAgentCoachmark.tsx
last_verified: 2026-08-19
stub: false
---

# GuideAgentCoachmark.tsx — one-shot "create your own agent" bubble

## Why it exists

The retired OnboardingChecklist's replacement on the nudge front: new users
whose first agent (the guide) was auto-created server-side get one bubble
pointing at the sidebar "+" saying they can create more themselves. Shown
while `lib/guideCoachmark` reports 'pending' (armed by the login path via
`is_new_user`); dismissing writes 'done', permanently.

## Upstream / Downstream

Mounted unconditionally in `MainLayout` (renders nothing unless pending AND
the anchor is measurable). Reads/writes via `lib/guideCoachmark`. i18n keys:
`onboarding.guideCoachmark.{text,gotIt}` (en + zh; other locales fall back).

## Design decisions

Rendering rides the shared `AnchoredCoachmark` (extracted 2026-08-19 after
review flagged that this file was a character-for-character copy of
MigrationCoachmark AND both could render pixel-overlapped on a local first
run — the shared component adds one-bubble-per-anchor queueing). This file
owns only the gate (localStorage via lib/guideCoachmark) and the copy; the
literal `t('onboarding.guideCoachmark.text')` call stays here because
zh-localization.test.ts asserts it at the file level.
