---
code_dir: frontend/src/components/runtime/
last_verified: 2026-04-10
---

# runtime/ — Panel showing live execution steps and long-term Narrative memory

This directory covers two very different time horizons in one tabbed panel:

- **Execution tab** — microsecond-to-seconds: the current agent pipeline
  (steps 0–5 + substeps) streaming live from the WebSocket.
- **Narrative tab** — minutes-to-days: the agent's long-term memory stored as
  Narratives → Module Instances → Events.

## Why they share one panel

Both are "what is the agent doing / has done" views. Putting them together
avoids a separate navigation entry and lets the user switch between "what's
happening right now" and "what happened before" with one click.

## Component tree

```
RuntimePanel
  ├── [execution tab]
  │     ├── ProgressRing (inline SVG)
  │     ├── KPICard (×3, local definition — pre-dates shared KPICard)
  │     └── StepCard (from steps/)
  └── [narrative tab]
        └── NarrativeList
              ├── NarrativeItem
              │     └── ModuleInstanceItem
              │           └── MemoryItem
              │                 └── EventCard
              └── (loading skeleton / empty state)
```

## Upstream / downstream

- Data: `useChatStore` (currentSteps, isStreaming), `usePreloadStore`
  (chatHistoryNarratives, chatHistoryEvents)
- Consumed by: main right-panel layout

## Gotchas

`RuntimePanel` defines its own local `KPICard` component instead of importing
the shared one from `@/components/ui`. This predates the shared KPICard
extraction. The two implementations are functionally equivalent but have
slightly different color maps. If you add a new color, add it to both.
