---
code_file: frontend/src/components/inbox/InboxPanel.tsx
last_verified: 2026-04-10
stub: false
---

# InboxPanel.tsx — LEGACY: Older inbox panel (not currently mounted)

Simpler predecessor to `AgentInboxPanel`. No KPI cards, no load-all, no newest-first sort within rooms. Still reads from `usePreloadStore.agentInboxRooms`.

Not mounted anywhere in the current app. If `AgentInboxPanel` is replaced, this file could serve as a starting point but would need updating to match current store API. Can be deleted.
