---
code_file: frontend/src/components/chat/OnboardingJourney.tsx
last_verified: 2026-06-20
stub: false
---

# OnboardingJourney.tsx — "JourneyBand" empty state for a fresh conversation

## Why it exists

When an agent is selected but the conversation has no messages yet, the empty
state used to be a single generic line ("Start a conversation"). The Narra
Agent App design ref replaces that blank moment with a JourneyBand: the
carbon·silicon binding-dot eyebrow, a short framing line, the three product
stations (Narra·Memory → Nexus·Network → Your Team) with a carbon pulse
travelling a dotted baseline, and suggested-prompt chips.

## Upstream / Downstream

- **Used by**: [[ChatPanel]] — rendered when `showEmptyState && agentId`.
- **Depends on**: [[identity|BindingDot]], `lucide-react` icons, NM tokens.
- **Talks back via**: `onPrompt(text)` → ChatPanel calls
  `composerRef.current.setText(text)` (see [[Composer]]). Chips FILL the
  composer; they do not send.

## Design decisions

- **Not the day-zero copy.** The literal "I just woke up" greeting stays in
  `BOOTSTRAP_GREETING` (shown for brand-new unnamed agents via
  `showBootstrapGreeting`, which precedes `showEmptyState`). This band is the
  generic fresh-start surface for any selected agent, so its framing is
  agent-neutral ("A fresh page.", weaves in the agent name).
- **Stations carry the brand spine**: carbon = Memory (Narra), silicon =
  Network (Nexus), ink = Team — same species semantics as the rest of the app.
- **Travelling pulse** uses the `.animate-travel` / `@keyframes nm-travel`
  added to `index.css`; it honors the global `prefers-reduced-motion` reduce
  block.

## Gotcha / edge cases

- Prompt strings are intentionally generic (no scenario hard-coding) per
  binding rule #4 — scenario-specific suggestions belong in Awareness, not in
  this presentational shell.
- The chip hover uses the existing `.hover-lift` utility (there is no `.chip`
  class in this codebase, unlike the standalone design ref).
