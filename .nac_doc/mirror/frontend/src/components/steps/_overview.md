---
code_dir: frontend/src/components/steps/
last_verified: 2026-04-10
---

# steps/ — Real-time execution step display for the agent pipeline

Displays the 7-step AgentRuntime pipeline as it streams in via WebSocket.
Each step can expand to show sub-details: Narrative selection reasoning,
Module loading decisions, instance relationship graphs, and tool execution
summaries.

## Why two components

`StepsPanel` is a legacy standalone panel (exists in the codebase for
historical reasons). `RuntimePanel` now re-uses `StepCard` directly in its
execution tab, which is the canonical location in the current UI. `StepsPanel`
is kept but may be vestigial.

## Data flow

Steps arrive from `useChatStore.currentSteps` via WebSocket streaming. Each
`Step` has a `details` blob that encodes display data, reasoning blocks,
and relationship graphs. `StepCard` interprets these details to show
human-readable context.

## Consumed by

`RuntimePanel` (execution tab) — primary consumer.
`StepsPanel` — secondary / possibly vestigial.
