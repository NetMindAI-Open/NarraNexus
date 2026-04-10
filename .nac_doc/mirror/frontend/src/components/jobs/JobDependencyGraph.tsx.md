---
code_file: frontend/src/components/jobs/JobDependencyGraph.tsx
last_verified: 2026-04-10
---

# JobDependencyGraph.tsx — React Flow canvas for job dependency visualization

Renders a directed acyclic graph of jobs where edges point from dependency to
dependent, using a custom topological layout algorithm.

## Why it exists

A flat list cannot show why a job is blocked. The graph makes it immediately
obvious which upstream job is holding back a chain.

## Upstream / downstream

- **Upstream:** `JobNode[]` passed from `JobsPanel` (already converted from
  API data)
- **Downstream:** Node click calls `onNodeClick(jobId)` → `JobsPanel` shows
  `JobDetailPanel`
- **Also used by:** `JobTemplateSelector` (preview mode with all-pending
  nodes, no click handler needed)

## Design decisions

**Layout algorithm:** Pure topological sort using memoised recursion
(`getTopologicalLevel`). Horizontal axis = dependency depth, vertical axis =
position within that level. No third-party layout library — keeps the bundle
small and the logic inspectable.

**Color scheme:** Hardcoded light-mode hex values (not CSS variables). This
was a deliberate choice because React Flow renders node styles as inline
`style={}` objects where CSS variables don't resolve. If the design system
shifts to dark mode, these colors need to be updated.

**Animated edges:** Edges leading into a `running` or `active` job are
animated (`animated: true`) to signal activity.

## Gotchas

- `useNodesState` / `useEdgesState` are initialised from a `useMemo` value
  but React Flow's internal state is **not** re-synced on subsequent prop
  changes. If jobs update (e.g., status change), the graph only re-renders
  if the `jobs` or `selectedJobId` reference changes — which is fine because
  `JobsPanel` recreates `jobNodes` via `useMemo([jobs, ...])`.
- If `jobs` contains dependency keys that don't match any job's `id` or
  `task_key` (stale references), those edges are silently dropped rather
  than crashing.
- The `paused` status has no entry in `statusColors` — it falls back to
  `statusColors.pending`.
