---
code_file: frontend/src/components/welcome/StepModel.tsx
last_verified: 2026-08-27
stub: false
---

# welcome/StepModel.tsx — welcome step 1, wire one model

## Why it exists

A thin wrapper so the flow reuses [[OneKeyOnboard]] — the same card Settings →
Providers shows — instead of growing a second provider list that would drift.
`hideHeader` + `bare` strip the card's own heading and surface because
[[WelcomeStepFrame]] already provides both.

- **Keeps the old /setup page's "Advanced setup" disclosure** ([[ProviderSettings]]
  behind a collapsed toggle). OAuth logins, custom endpoints and per-slot models
  can't be expressed as one pasted key, and sending those users to Settings
  before they have finished signing up is a dead end.

## Gotcha

- Skippable by design; the consequence is handled by [[NoProviderNotice]].
- The step disappears entirely once the user has any provider — cloud accounts
  arrive with a free-tier card, so they usually never see it ([[welcomeSteps]]).
