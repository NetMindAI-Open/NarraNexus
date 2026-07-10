---
code_file: frontend/src/components/chat/InnerThoughtCard.tsx
last_verified: 2026-07-03
---

## 2026-07-03 — per-source colour + name (scannable), icons dropped

Every activity used to render identically ("Message" + one MessageCircle
icon) — a wall of indistinguishable rows. Each working_source now has its own
COLOUR (SOURCE_META) shown as a left accent bar + a coloured dot + the source
name; IM channels use their brand name verbatim (WeChat / Slack / Telegram /
Discord / NarraMessenger), category sources (job / collaboration / skill /
callback) use a localized label, unknown falls back to a generic activity
label. Per-source ICONS were dropped on purpose — lucide has no brand logos,
so colour + name carries the identity honestly. Expand/lazy-load of the
agent-loop steps (getEventLog + timeline/thinking fallback, distinct
loading/error/empty states) is unchanged.

# InnerThoughtCard.tsx — one inner-thought (activity) as an expandable card

Renders a ``message_type=activity`` row in the chat's Inner Thoughts tab. An
activity is written whenever a NON-chat trigger runs the agent and it sent no
user-facing reply (chat_module.py) — those triggers are diverse (scheduled
job, agent-to-agent bus, inbound IM on any of six channels, skill study), so
the card is headed by ``item.workingSource`` (icon + i18n source name via
SOURCE_META) rather than a flat "Background activity" line.

The turn's steps live in the events table and are fetched lazily by
``item.eventId`` via ``api.getEventLog`` (same endpoint + EventLogResponse
shape MessageBubble uses) — only on first expand, cached in a small
``LoadState`` state machine. ``toEntries`` prefers the response ``timeline``
(EventLogTimelineEntry: type thinking/tool_call/tool_output/native_output/
reply, content/tool_name/tool_input/tool_output) and falls back to
(thinking, tool_calls) for old backends. States are distinct: loading /
load-FAILED / genuinely-EMPTY, and there is no expander when the activity has
no event_id. Self-renders the small step list (not TurnTimeline) to stay
self-contained. i18n keys: ``chat.inner.*`` in all 10 locales. Guarded by
__tests__/InnerThoughtCard.test.tsx (7 tests).
