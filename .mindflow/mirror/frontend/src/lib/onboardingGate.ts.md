---
code_file: frontend/src/lib/onboardingGate.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 (评审修订) — 「哪些卡是 login 自动开的」交回后端

删掉 `AUTO_PROVISIONED_PROVIDER_SOURCES`，改读每张卡的 `auto_provisioned`
（GET /api/providers，由后端在 provisioner 旁派生）。前端那份集合与后端事实没有机械约束，
后端多开一张卡的那天所有新用户都会被判成老用户、首程流程静默消失——正是本文件存在
要防的 bug。顺带删掉一行紧接着被覆盖的死 `inFlight.set`（M7）。

# lib/onboardingGate.ts — "does this user still owe the first-run flow?"

## Why it exists

Because `/` is only one way into the app. The first version of this gate lived in
[[App]]'s `RootRedirect`, which meant a brand-new account could walk straight
past [[WelcomePage]] by arriving any other way: a `?next=/app/chat` login (what
the website CTAs and the /pay bounce use), a bookmark, or a plain refresh on a
protected route. Owner caught it 2026-08-27 — "a new account must go through
welcome first". So the question moved here and `ProtectedRoute` asks it, which
covers every protected entry point at once.

## Design decisions

- **Server-side answer** (`onboarding_progress.landing_completed`), not
  localStorage: a second browser or machine must not replay the flow.
- **Cached per userId in module scope + in-flight dedupe.** It is asked on every
  protected mount; N simultaneous mounts cause ONE request, and the truth only
  changes when the flow writes the flag.
- **`markWelcomeSeen()` flips the cache before the flow navigates away.**
  Without it, ProtectedRoute would re-run the gate on the destination route,
  read a stale "still owes it", and bounce the user back into the flow.
- **Existing users are backfilled silently** — but "existing" has to mean
  something a HUMAN did, because login itself creates artifacts. The first
  version counted "any agent or any provider" and so misclassified EVERY new
  account (Owner report 2026-08-27: a fresh login landed straight in the chat,
  flag written behind their back). Login provisions a guide agent for every new
  user, and auto-registers NetMind provider cards on the Power path. The probes
  therefore discount both: agents other than the guide
  ([[guideAgent]]'s `pickGuideAgent`), and providers whose `source` is not in
  `AUTO_PROVISIONED_PROVIDER_SOURCES`.
- **A failing probe means "let them in"**, never "hold them". Onboarding state is
  not worth blocking someone's first minute over; the worst case is asking again
  next session.
- `clearOnboardingGateCache()` is called by [[sessionWipe]] — module scope
  survives a store reset, so the next user must not inherit this answer.
