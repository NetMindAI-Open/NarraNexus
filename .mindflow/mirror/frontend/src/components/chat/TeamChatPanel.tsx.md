---
code_file: frontend/src/components/chat/TeamChatPanel.tsx
last_verified: 2026-07-22
stub: false
---

## 2026-07-21 — voice input (mic), parity with single-agent chat

Tools row gained an `AudioRecorder` (mic) next to the attach `+`. Records → uploads with
`source:'recording'` → backend Whisper → the memo joins `pending` and renders as a
`VoiceTranscript` chip; on send it flows like any bus attachment (agents get the transcript
in their marker). Reuses the ChatPanel plumbing: a mount `getTranscriptionAvailability`
probe, a click-time `onPreflight` re-check, a "voice unavailable" `<Dialog>` (→ Settings),
and a post-record notice banner. New i18n keys `chat.team.transcriptionUnavailable|
voiceUnavailableTitle|voiceUnavailableBody|voiceUnavailableProbeFailed|openSettings|cancel`.

## 2026-07-21 — user can attach files in the composer

Composer gained a paperclip button + hidden multi `<input type=file>`. Picked files upload
immediately via `api.uploadTeamChatAttachment` into a `pending: BusAttachment[]` state,
shown as removable chips above the textarea; `handleSend` passes `pending` to
`api.sendTeamChat` and clears it (restores on failure). An attachment-only message (no text)
is allowed. New i18n keys `chat.team.attach|uploading|removeAttachment` (en+zh).

## 2026-07-20 — bus attachments render in the room

Each message bubble now renders `<BusAttachmentList attachments={m.attachments} />`
below the text, so files an agent sent/shared into the team room show as
chips/thumbnails. `TeamChatMessage` gained `attachments?: BusAttachment[]`
(populated by `GET /api/teams/{id}/chat/messages`). See [[BusAttachmentList]].

# chat/TeamChatPanel.tsx — Team group-chat surface

## Why it exists

The user-facing view of the homepage's "agent team": one team's shared room,
rendered in the SAME main slot as the single-agent [[ChatPanel]] so switching
between an agent and a team is seamless (see [[MainLayout]]'s `TeamChatView`).

## How it works

Messages flow over the **message bus**, NOT a single-agent narrative:
- **Send** → `api.sendTeamChat(teamId, content, mentions)` → `POST
  /api/teams/{id}/chat/messages`. The composer text's `@tokens` are resolved to
  member `agent_id`s (or the literal `"@all"`); the backend posts as the
  synthetic sender `usr_<user_id>` and maps `@all` → bus `"@everyone"`.
- **Transcript** → polls `GET /api/teams/{id}/chat/messages` every `POLL_MS`
  (3s). The response also carries `thinking: string[]` — members the bus trigger
  is currently processing — which drives the "…" typing bubbles.

## Design decisions / gotchas

- **@-mention autocomplete** with an `@all` option pinned on top; keyboard
  (↑↓/Enter/Tab/Esc) + click. `@all` is a `MentionOption` kind, not a member.
- Bubbles mirror the single-agent [[MessageBubble]]: carbon-soft (user, right,
  carbon right-edge) vs silicon-soft (agent, left, silicon left-edge), meta row
  outside the bubble. Agent content is rendered through `<Markdown>` (the replies
  are markdown); user content stays plain text. `.content.trim()` + the global
  `.markdown-content > :first-child/:last-child { margin: 0 }` rule keep the
  agent bubble's vertical padding equal to the user bubble's.
- The room itself is created/owned by the backend (`created_by = team_<id>`);
  this panel never touches the bus directly — it only calls the two team-chat
  routes. Agent replies (and agent→agent @ cascades) are produced server-side by
  the MessageBusTrigger and just appear in the polled transcript.

## 2026-07-22 — team activity visualization

Consumes the new `activity` from `getTeamChat` ([[teams]]). Renders a top **status strip**
(chip per running/queued member: dot + name + phase·elapsed) and, at the bottom of the
timeline, an **activity bubble** per active member — running shows a spinner + live phase
(思考中 / 调用 <tool> / 回复中) + elapsed; queued shows the "…" dots. A 1s ticker advances
elapsed between the 3s polls. Replaces the old dumb `thinking` "…" bubbles. i18n
`chat.team.activity.*`.
