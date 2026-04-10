---
code_file: frontend/src/components/steps/StepCard.tsx
last_verified: 2026-04-10
---

# StepCard.tsx — Expandable card for one agent pipeline step

The richest display component in the steps directory. A step can carry
structured `details` that `StepCard` interprets to render human-readable
context:

- `display.items` — list of Narratives (with similarity scores) or Module
  Instances (with status badges)
- `execution` — which execution path was chosen (e.g., "AgentSDK + tools")
- `selection_reason / decision_reasoning` — LLM's textual reasoning
- `changes_summary` — added/removed/updated instance diffs
- `relationship_graph` — Mermaid-style ASCII text of the module dependency
  graph

## Why it exists

The backend streams rich step details to the frontend so users can inspect
*why* the agent made each decision, not just *that* it made them. StepCard
is the single place that parses and renders all of this.

## Upstream / downstream

- **Upstream:** `Step` type from `@/types`, passed from `RuntimePanel` or
  `StepsPanel`
- **Used by:** `RuntimePanel` (execution tab), `StepsPanel`

## Design decisions

**Auto-expand for running steps:** `expanded` state initialises to
`step.status === 'running'`. Running steps expand immediately so the user
sees progress without clicking.

**Cancelled/archived filter:** The `displayData.items` loop skips items
where `status === 'cancelled' || status === 'archived'`. This keeps the
step display clean when instances are tombstoned.

**Sub-component strategy:** Three small helper components (`ReasoningBlock`,
`RelationshipGraph`, `ChangesSummary`) are defined inline in this file. They
are not exported — they are implementation details of `StepCard`.

## Gotchas

All `details.*` accesses use `as SomeType` casts because the Step type stores
details as `unknown`. If the backend changes a key name (e.g., renames
`selection_reason` to `narrative_selection_reason`), the cast will return
`undefined` silently. There are no TypeScript errors — you'll notice via
missing UI sections.
