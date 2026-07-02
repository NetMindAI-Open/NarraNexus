---
code_file: frontend/src/lib/arenaLanding.ts
last_verified: 2026-06-23
stub: false
---

# arenaLanding.ts — front-end Arena landing flow

## Why it exists

Closes the loop for a user arriving from arena42.ai: once they're logged in,
provision (or reuse) their Arena agent and open it — fast. The signal is
`source=arena` on the inbound URL, stashed in sessionStorage by
`takeInboundToken`. This module reads that signal, calls the idempotent backend
`provisionArena()`, then refreshes the left agent panel and selects the new
agent.

## Upstream / Downstream

**Triggered by:** `App.tsx` — a mount call (already-logged-in case) plus a
`useConfigStore.subscribe` on the `isLoggedIn` transition (logs-in-after-landing
case, incl. the inbound-token and LoginPage paths). **Calls:**
`api.provisionArena(netmindToken)` (POST /api/arena/provision) — it reads the
user's NetMind JWT from `configStore.netmindToken` and forwards it so the backend
can bind the agent's owner email (optional; absent → bind skipped). **Mutates
stores:**
`configStore.refreshAgents()` then `setAgentId`, and `chatStore.setActiveAgent`
— the left panel subscribes to `configStore.agents`, so it re-renders the new
agent immediately.

## Design decisions

**Idempotent and self-guarding.** An in-flight flag plus a per-user
`nx-arena-provisioned` sessionStorage marker prevent duplicate work; the backend
is idempotent regardless. Selection sets both stores: `configStore.agentId`
drives the sidebar, `chatStore.activeAgentId` drives the chat session.

**"Fast" = list refresh, not optimistic insert.** After provision returns an
`agent_id`, a single `getAgents()` reload surfaces it; this keeps one source of
truth (the backend) rather than synthesizing an agent client-side.

## Gotchas

- Reads `source` from both sessionStorage AND the live URL, because a user can
  arrive with `?source=arena` and no token (already has a Power session
  elsewhere) — `takeInboundToken` still stashes the source in that case.
- No-op until `isLoggedIn && userId`; the subscribe handles the deferred case.
