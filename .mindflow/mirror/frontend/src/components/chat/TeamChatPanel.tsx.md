---
code_file: frontend/src/components/chat/TeamChatPanel.tsx
last_verified: 2026-06-23
stub: false
---

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
