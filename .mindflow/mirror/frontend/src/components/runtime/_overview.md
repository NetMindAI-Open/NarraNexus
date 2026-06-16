---
code_dir: frontend/src/components/runtime/
last_verified: 2026-06-10
---

# runtime/ — Narrative memory components (post-2026-06-10 slim-down)

`RuntimePanel` (the old Execution/Narrative tabbed panel) was retired in
the bookmark-strip redesign: live execution is fully covered by the
chat-inline TurnTimeline, and the Narrative view moved into the Agent
profile drawer's "Memory" section ([[AgentProfilePanel]]). The
`components/steps/` family (StepCard / StepsPanel) died with it — its
only consumer was RuntimePanel.

What remains here:

```
NarrativeList            ← hosted by AgentProfilePanel (Memory section)
  ├── NarrativeItem
  │     └── ModuleInstanceItem
  │           └── MemoryItem
  │                 └── EventCard
  └── (loading skeleton / empty state)
```

## Upstream / downstream

- Data: `usePreloadStore` (chatHistoryNarratives, chatHistoryEvents)
- Consumed by: `components/bookmarks/AgentProfilePanel`

## Gotchas

`AgentProfilePanel` imports `NarrativeList` from this module path
directly (not the barrel) so test mocks can intercept the submodule.
