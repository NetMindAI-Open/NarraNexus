---
code_file: frontend/src/lib/welcomeSteps.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 (评审修订) — `mode` 直接用 `AppMode`

不再私造 `'local' | 'cloud'` 词汇（评审 M6）：`cloud-web ? 'cloud' : 'local'` 是负向匹配，
第三个 AppMode 会被静默当成 local 去探测文件系统。内部只判 `mode === 'local'`。

# lib/welcomeSteps.ts — which first-run steps apply, in order

## Why it exists

[[WelcomePage]] must not branch per deployment. This pure function answers
"which steps does THIS user still owe?" so the question is a unit test
(`lib/__tests__/welcomeSteps.test.ts`) instead of something you discover by
signing up on production.

## Design decisions

- **Fixed order `model → import → agent`** (Owner decision 2026-08-27). Meeting
  the guide agent is always last so the closing CTA drops the user into a
  conversation that actually works — model wired, history imported — and so the
  agent's fire-and-forget provisioning has had two screens to finish.
- **Steps are dropped, never disabled.** A step the user cannot act on is worse
  than a step that isn't there: cloud has no user filesystem (`/detect` 503s),
  an empty machine has nothing to import, and a deployment with guide
  provisioning off has no agent to introduce.
- **The model step is unconditional** (2026-08-27 fix). It was gated on
  `providerCount === 0`, which meant it never appeared: login auto-registers
  NetMind cards (`_provision_providers` in `backend/routes/auth.py` — free tier
  and/or the user's own Power account), so a brand-new account reaches the flow
  with two providers already, and those rows are indistinguishable from a key the
  user pasted. Owner hit it immediately ("why only two steps — where is the
  provider one?"). Since the flow only runs for genuinely new accounts (existing
  users are backfilled by [[onboardingGate]]), showing the step and letting them
  skip in one click is both simpler and correct.
- **`shouldProbeDetections`** exists so the caller doesn't even *call* detect on
  cloud. A 503 on the first screen after signup is a bad first impression for a
  step that could never have appeared.
- An empty result is legitimate (`isWelcomeFlowEmpty`) — the page records the
  flow as done and redirects rather than rendering an empty shell.

## Gotcha

- The `mode` vocabulary here is `local | cloud`, while runtimeStore's `AppMode`
  is `local | cloud-web`. [[WelcomePage]] maps between them; keep the mapping
  there rather than teaching this file a store's enum.
