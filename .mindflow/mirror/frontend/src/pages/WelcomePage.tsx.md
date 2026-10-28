---
code_file: frontend/src/pages/WelcomePage.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — mode 不再映射

`buildWelcomeSteps` / `shouldProbeDetections` 直接吃 runtimeStore 的 `AppMode`；null 只在
ProtectedRoute 尚未放行时出现，按 local 处理与原语义一致。

# pages/WelcomePage.tsx — the first-run flow

## Why it exists

Replaces three disconnected surfaces a new user used to cross with no sense of
where they were (Owner decision 2026-08-27): the `/setup` provider page, the
import offer that popped over the chat, and the coach-mark announcing the
auto-provisioned guide agent. Now: one page, a left rail of steps, one thing per
screen — wire a model → import what's on this machine → meet the guide agent →
straight into its conversation.

Reached from **any** protected route: `ProtectedRoute` consults
[[onboardingGate]] and redirects here with `?next=<where they were headed>`, so a
deep link or a refresh can't skip the flow (Owner 2026-08-27). The flow hands
that destination back on exit — except when the user opens the guide agent, which
always ends in that agent's conversation, because that is what the CTA promised.

## Design decisions

- **Data-driven composition, never per-deployment branches.**
  [[welcomeSteps]] returns the applicable steps; cloud simply has no import step
  and is not even probed. An empty result means "nothing to onboard": the page
  records the flow as done and redirects instead of rendering an empty shell.
- **`landing_completed` is written server-side, once, on ANY exit** — finished,
  skipped, or nothing-to-do (`users.metadata.onboarding_progress`, write-once
  true). localStorage would have replayed the flow on a second browser; the
  write is best-effort because blocking a user's first minute on an analytics-
  grade flag is worse than showing the flow twice.
- **One probe on mount** (providers + agents + detections in parallel) feeds both
  the step list and the steps themselves, so no step re-fetches what the page
  already knows.
- **Imports are refreshed into the sidebar on exit** (`onImported(..., { open:
  false })`) — an agent that landed during the flow must be visible on arrival,
  but the flow decides where the user goes, not the hook.
- **`markWelcomeSeen()` is called before navigating away**, so the gate on the
  destination route doesn't read a stale answer and bounce the user back.
- **Skipping the import step arms the "+" coach-mark** so declining still teaches
  where import lives; opening the guide agent dismisses the guide coach-mark,
  since the flow already introduced it.

## Gotchas

- `WelcomeFallback` is a local copy of App's private spinner; importing across
  App ↔ pages would be circular. One shared spinner is a separate cleanup.
- runtimeStore's `AppMode` is `local | cloud-web`; [[welcomeSteps]] speaks
  `local | cloud`. The mapping lives here on purpose.
- Analytics: `welcome_entered / welcome_completed / welcome_skipped` replaced the
  `setup_*` funnel events when /setup became step 1.
