---
code_file: frontend/src/components/runtime/RuntimePanel.tsx
last_verified: 2026-04-10
---

# RuntimePanel.tsx — Tabbed panel combining live execution and Narrative history

## Why it exists

The agent pipeline produces two outputs that users need to inspect:
1. The current run's step-by-step execution (ephemeral, lives in `useChatStore`)
2. The persisted Narrative memory that accumulates over many conversations

Merging them into one tabbed view avoids a separate sidebar entry for each.

## Upstream / downstream

- **Upstream:** `useChatStore` (currentSteps, isStreaming), `usePreloadStore`
  (chatHistoryNarratives, chatHistoryEvents, refreshChatHistory),
  `useConfigStore` (agentId / userId)
- **Downstream:** `StepCard` (from `steps/`), `NarrativeList`
- **Consumed by:** right-panel tab layout

## Design decisions

**Step counting:** Only steps with integer IDs (`/^\d+$/.test(s.step)`) count
toward the progress percentage. Sub-steps use dot notation like `"2.1"` and
are excluded from the progress ring, because they are implementation details
of step 2, not separate pipeline stages.

**Progress ring:** SVG circle drawn inline. Capped at 99% while streaming so
it never shows 100% prematurely — the actual completion snaps to 100% once
streaming ends.

**Local KPICard:** Defined inline in this file (pre-dates the shared component
in `@/components/ui`). See `runtime/_overview.md` Gotchas for implications.

## Gotchas

Refresh button only appears on the Narrative tab. Calling `refreshChatHistory`
without agentId/userId is a no-op (early return in the store), so the guard
`if (agentId && userId)` before calling it is load-bearing.
