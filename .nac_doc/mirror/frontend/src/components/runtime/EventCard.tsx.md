---
code_file: frontend/src/components/runtime/EventCard.tsx
last_verified: 2026-04-10
---

# EventCard.tsx — Expandable card for a single agent Event

An Event is one complete request-response cycle: user input → agent thinking
→ tool calls → final output. EventCard renders the collapsed summary (input
preview + trigger info) and expands to show all four sections.

## Upstream / downstream

- **Upstream:** `ChatHistoryEvent` from `usePreloadStore`, passed via
  `NarrativeList → MemoryItem`
- **Downstream:** `EventLogEntry` sub-component (inline in same file)
- **Used by:** `MemoryItem` inside `NarrativeList`

## Design decisions

**User input extraction:** Searches `event_log` for entries with type
`user_input`, `input`, or `trigger`. The first match is used as the card
header preview. If none, falls back to `trigger_source` or a generic label.

**Display log filtering:** `event_log` entries of type `user_input`, `input`,
`trigger` are excluded from the expandable log section (already shown in the
header) to avoid duplication.

**EventLogEntry color coding:** Each log entry type (thinking, tool_call,
tool_result, error, message_output) has a distinct background/border color
from the CSS variable palette.

## Gotchas

Long log entry content is expandable inline with a "...more" toggle. The
threshold is 100 characters. Very dense tool call results (common in job
modules) hit this threshold often — each entry will show a truncated preview
by default.
