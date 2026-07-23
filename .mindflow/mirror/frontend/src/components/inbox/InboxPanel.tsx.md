---
code_file: frontend/src/components/inbox/InboxPanel.tsx
last_verified: 2026-07-20
stub: false
---

## 2026-07-20 — bus attachments in the simple inbox

Renders `<BusAttachmentList attachments={msg.attachments} />` after each message's
content (`RoomMessage.attachments`). Same shared component as [[AgentInboxPanel]].
See [[BusAttachmentList]].

## 2026-05-28 — clicking the channel row clears that channel's unread

Same click-to-clear refactor as [[AgentInboxPanel.tsx]] —
`toggleRoom` now always calls `api.markAgentRoomRead(roomId, agentId)`
when the room has unread, regardless of expand direction or
loaded-message count. See the AgentInboxPanel mirror for the full
rationale + the backend endpoint design.

# InboxPanel.tsx — LEGACY: Older inbox panel (not currently mounted)

Simpler predecessor to `AgentInboxPanel`. No KPI cards, no load-all, no newest-first sort within rooms. Still reads from `usePreloadStore.agentInboxRooms`.

Not mounted anywhere in the current app. If `AgentInboxPanel` is replaced, this file could serve as a starting point but would need updating to match current store API. Can be deleted.
