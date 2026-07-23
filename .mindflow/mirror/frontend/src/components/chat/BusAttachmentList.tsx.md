---
code_file: frontend/src/components/chat/BusAttachmentList.tsx
last_verified: 2026-07-20
stub: false
---

# BusAttachmentList.tsx — render files attached to bus messages

## Why it exists

Message-bus messages (agent-to-agent DMs, team group chat) can now carry files
(the multimodal-A2A feature — see backend
`message_bus/_bus_attachment_impl.py`). This component renders those files under
a message: image → inline thumbnail, everything else → a downloadable chip
(name + `category · KB`).

## Why not reuse the chat AttachmentImage / MessageBubble block

Chat attachments are addressed per-agent by `file_id` and served from
`/api/agents/{agent_id}/attachments/{file_id}/raw`. **Bus attachments live in the
per-user shared area** and are addressed by `rel_path`, served by
`GET /api/agent-inbox/attachments/raw?path=<rel_path>`. So this is a parallel,
bus-specific renderer using [[useBusAttachmentBlobUrl]] / `api.fetchBusAttachmentBlob`,
not the agent-scoped `AttachmentImage`.

## Upstream / downstream

- **Consumes** `BusAttachment[]` (`types/messages.ts`), present on
  `TeamChatMessage` (`types/teams.ts`) and `RoomMessage` (`types/api.ts`).
- **Rendered by** [[TeamChatPanel]], [[AgentInboxPanel]], [[InboxPanel]] — one shared
  component so all three surfaces render bus files identically.
- **Auth**: both the thumbnail hook and the download path go through
  `api.getAuthHeaders()` (JWT / X-User-Id) because `<img src>` / `<a href>` can't
  attach headers themselves.

## 2026-07-21 — voice memos

An attachment with `source==='recording'` renders as a `VoiceTranscript` (the transcript IS
the content) instead of a thumbnail/chip — matching the single-agent MessageBubble.

## Scope note

MVP surfaces chips + thumbnails + download. Rich preview (PDF inline, etc.) is a
possible follow-up.
