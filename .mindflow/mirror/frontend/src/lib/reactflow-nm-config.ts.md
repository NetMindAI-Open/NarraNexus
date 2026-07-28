---
code_file: frontend/src/lib/reactflow-nm-config.ts
last_verified: 2026-05-18
stub: false
---

# reactflow-nm-config.ts — NM-styled defaults for ReactFlow

## Why it exists

ReactFlow has no theme system — every node and edge style is per-instance
or via custom nodeTypes. To keep JobsPanel (and any future graph view)
aligned with the NM design system without per-component duplication, this
module centralizes the NM-conforming defaults:

- `defaultEdgeOptions`: 1.5px ink-50 stroke + smoothstep
- `speciesColors`: the four NM kinds for node identity (Carbon/Silicon/
  Overlap/ink)
- `proOptions.hideAttribution`: hide ReactFlow watermark (NarraNexus is
  not a ReactFlow product showcase)
- `defaultViewport`: centered at zoom 1
- `getNMNodeStyle(kind)`: helper returning the base CSSProperties for a
  node by NM kind. Custom nodeTypes spread this then add decoration
  (status pulse, bracket-edge, etc.)

## Upstream / Downstream

- **Upstream**: ReactFlow 11 `<ReactFlow>` props (`defaultEdgeOptions`,
  `proOptions`, `defaultViewport`).
- **Downstream**: `components/jobs/JobsPanel.tsx` (M4 will rewire it to
  consume these defaults). Any future graph in NarraNexus.

## Design decisions

**Node kind as semantic identity, not styling.** A node is `user` / `agent`
/ `tool` / `output` — that's a NarraNexus domain concept. The mapping to
NM colors lives in this file so the domain stays clean.

**Edge type = smoothstep.** ReactFlow's `default` (straight) edge feels
sharp on the warm paper. `smoothstep` curves at 90° turns and reads as
"a thought flowing through the graph" — matches NM motion language
(Axiom #8: paper rhythm, no spring).

**Edge stroke = ink-50 (`rgba(42,38,32,0.50)`), not species color.** Edges
carry no identity — they're just connections. Coloring edges by source
node species would visually overwhelm. Selected/highlighted edges can
override per-edge.

**`hideAttribution: true`.** ReactFlow shows "React Flow" badge in
bottom-right by default. NarraNexus is the product, not ReactFlow.

## Gotchas

- `CSSProperties` is imported as a type from `react`. If a future change
  removes the React typings dependency, replace with a self-defined
  partial type.
- Border color override happens via inline style. ReactFlow nodes apply
  this style to their wrapper `<div>` — for selected state, ReactFlow
  adds its own `.react-flow__node-{type}.selected` rule with a 2px outline
  that may conflict. M4 JobsPanel implementation should override the
  selected outline to use NM BracketCornerMarks instead.

## Related

- `lib/echarts-nm-theme.ts` — sibling NM bridge for charts
- `lib/__tests__/reactflow-nm-config.test.ts` — unit test
- `components/jobs/JobsPanel.tsx` — primary consumer (rewired in M4)
