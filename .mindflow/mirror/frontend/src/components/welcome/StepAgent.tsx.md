---
code_file: frontend/src/components/welcome/StepAgent.tsx
last_verified: 2026-08-27
stub: false
---

# welcome/StepAgent.tsx — welcome step 3 (always last), meet the guide agent

## Why it exists

The guide agent used to arrive unannounced: a new user landed in a chat with an
agent they never created, plus a coach-mark pointing at "+". This screen names
it — persona, opening line, what it does — so the closing CTA lands somewhere the
user recognises. It is also the flow's exit into the product.

## Design decisions

- **The CTA is product-level — "Start using NarraNexus" — sitting above the
  skip** (Owner 2026-08-27). It reads correctly whether or not the guide agent
  has landed, so there is no second label for the not-ready case; the agent's
  name still carries the heading and the card.

- **Polling with a skeleton, and a hard bail-out.** Provisioning is
  fire-and-forget at login ([[provisioning.py]]), so the agent may not exist yet.
  Being last makes that rare; after `GUIDE_WAIT_MS` (3s) the CTA becomes "go to
  the app" rather than holding the user on the final screen.
- **Only the first paragraph of the greeting** is previewed. The stored greeting
  is bilingual (EN, `---`, 中文) because it is rendered when the user's locale is
  unknown; the full text is the conversation's first message, not this card's job.
- **Rename is inline and collapsed** (`RenameDisclosure` → `api.updateAgent`).
  The generated name is usually fine, but sending someone to Settings
  mid-onboarding to fix a name they dislike is worse than the name. Persona
  editing is deliberately NOT here — that means editing Awareness, which has a
  real editor ([[AwarenessPanel]]) and does not belong smuggled into a first-run
  step.
- `pickGuideAgent` lives in [[guideAgent]], not here — a component file may only
  export components (fast refresh), and [[welcomeSteps]] needs the same answer.
- Opening the agent dismisses the guide coach-mark ([[guideCoachmark]]): the flow
  has just introduced it by name, so the bubble has nothing left to say.
