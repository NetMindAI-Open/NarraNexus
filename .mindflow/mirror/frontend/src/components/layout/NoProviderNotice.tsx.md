---
code_file: frontend/src/components/layout/NoProviderNotice.tsx
last_verified: 2026-08-27
stub: false
---

# layout/NoProviderNotice.tsx — "you skipped wiring a model" strip

## Why it exists

The welcome flow's model step is skippable (Owner decision 2026-08-27) — a user
without an API key must not be trapped on screen one. This strip is the other
half of that decision: it names the reason nothing replies instead of letting the
user discover it as a failed message. "Wire one now" jumps back to that step.

## Design decisions

- **Local only.** Cloud gets a free-tier provider card at first login, so there
  is nothing to warn about.
- **One probe per mount**, not polling: it is a hint, and a provider added later
  in the session shows up on the next load.
- **Self-clearing**: it disappears the moment a provider exists, so fixing the
  cause also removes the message.
- Dismissible per user (localStorage) and never re-arms — the user is allowed to
  say "I know".
- Silent on a failed probe: crying wolf because the backend was slow to start is
  worse than saying nothing.
