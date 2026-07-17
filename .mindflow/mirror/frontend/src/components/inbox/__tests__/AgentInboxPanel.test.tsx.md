---
code_file: frontend/src/components/inbox/__tests__/AgentInboxPanel.test.tsx
last_verified: 2026-07-13
stub: false
---

# AgentInboxPanel.test.tsx — message-card rendering contract

## 为什么存在

Locks in the 2026-07-13 card-list redesign of the expanded room's
message list in [[AgentInboxPanel.tsx]]: one card per message, sender
name + relative time in the card header, and a per-sender visual
identity (initials dot + left-accent) that is stable for the same
sender and distinct across senders.

## 设计决策

Stores (`@/stores`) and the API layer are module-mocked, so the panel
renders against a fixed two-sender room fixture with `unread_count: 0`
(avoids the mark-read side call in `toggleRoom`). Sender distinction is
asserted by comparing whole card `className` strings rather than
specific palette classes — the test cares that senders LOOK different,
not which color the hash picked. Relative time is asserted via the
`inbox-message-time` testid because `formatRelativeTime` output depends
on the wall clock.

## Gotcha / 边界情况

`vi.mock('@/stores')` references the `ROOMS` const declared later in
the file — legal because vitest hoists mocks and the factory only runs
at import time of the component, after module evaluation.
