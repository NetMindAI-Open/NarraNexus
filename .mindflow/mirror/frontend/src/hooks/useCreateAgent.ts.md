---
code_file: frontend/src/hooks/useCreateAgent.ts
last_verified: 2026-05-21
stub: false
---

# useCreateAgent.ts — shared "create a blank agent" action

## Why it exists

Agent creation is triggered from two places: the sidebar `AgentList`
button and the `OnboardingChecklist` card. Before this hook the logic
lived only in `AgentList.handleCreateAgent`; duplicating it into the
checklist would have let the two drift (forget `setActiveAgent`, forget
the onboarding side effect, etc.). The hook is the single create path.

## What it owns

1. `api.createAgent` call
2. Store wiring — prepend to `configStore.agents`, set it active in both
   `configStore` (agentId) and `chatStore` (setActiveAgent, clears badge)
3. Onboarding side effect — fires `markOnboardingStep('first_agent_created')`
   fire-and-forget on success

## Design decisions

**Onboarding mark is mode-agnostic + best-effort.** It fires in local
mode too (cheap, harmless — only the checklist *card* is cloud-gated) and
its failure is swallowed so it can never block or error agent creation.

**Stores read via `getState()`, not hook subscriptions.** `createAgent` is
a `useCallback` with an empty dep array; reading the stores imperatively
inside keeps the callback stable and avoids stale-closure bugs.
