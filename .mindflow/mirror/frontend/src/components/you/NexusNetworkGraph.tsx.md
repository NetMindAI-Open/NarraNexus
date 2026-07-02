---
code_file: frontend/src/components/you/NexusNetworkGraph.tsx
last_verified: 2026-06-23
stub: false
---

# NexusNetworkGraph.tsx — the Nexus Network tab (real data)

## 为什么存在

The owner-level social graph of the "You" workspace ([[YouWorkspace]]):
everyone the user's agents know, MERGED across agents. Data:
`api.getMyNetwork()` → `GET /api/me/network` ([[me]]), which dedupes the same
entity seen by several agents into one node carrying `known_by`.

## 设计决策

- Radial SVG with **concentric familiarity rings** (two dashed guide circles,
  `var(--text-tertiary)` @ .7 — bumped up from the too-faint hairline; labelled
  `direct` / `known of`): **you** at the centre (carbon, marked `is_self`),
  entities sit ON the ring matching their familiarity, coloured by kind
  (people=carbon, agents=silicon, groups=`#8E5CB8`; the backend reconciles type
  against the agent roster so teammates always read silicon — see [[me]]).
  Distance from you = closeness; node size + edge weight grow with
  `known_by.length` — the cross-agent signal
- **Gentle rotation**: the edges + nodes orbit you in a `.nx-spin` group
  (~100s/rev); rings, tier labels and centre stay fixed. Each node label
  counter-rotates (`.nx-unspin`, `transform-box: fill-box`) so it stays upright
  while orbiting. Disabled under `prefers-reduced-motion`.
- **Search**: optional `search` prop filters outer nodes by name / description
  / known_by / expertise (driven by the [[YouWorkspace]] search box).
  (relationship_strength is still ~0 on fresh data, so headcount carries the
  weight). Deterministic even-angle layout — no force sim — so it stays stable
  and cheap for the small graphs this produces.
- Nodes are real buttons (`role`, `tabIndex`, Enter/Space) with a soft glow
  ring on hover/selection signalling clickability. Click → detail (kind,
  familiarity, description, `known by <agents>`, interactions, last seen).
- Owner-scoped: never reads the selected `agentId`.

## 新人易踩的坑

- The owner's own entity (`is_self`) is the centre, NOT an outer node — filter
  it out of the ring. The centre label comes from `configStore` (displayName /
  userId), not from the entity.
- Empty (no non-self entities) is a normal cold-start → empty state, not error.
  Agents only accrue social entities once they actually interact.
