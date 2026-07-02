---
code_file: frontend/src/pages/YouWorkspace.tsx
last_verified: 2026-06-23
stub: false
---

# YouWorkspace.tsx — the owner-scoped "You" workspace

## 为什么存在

The right rail ([[BookmarkStrip]]) is **agent-scoped**: headed by `<agent>`,
it shows ONE agent's Config / Memory / Network. There was no symmetric
**owner-scoped** home — a place that aggregates ACROSS all your agents and
answers "what does my whole agent-world know / believe about *me*". This page
is that home, and the left sidebar's carbon user avatar is its entry (the
carbon counterpart to clicking a silicon agent). Owner-decided IA: avatar = me
= my world + my notes; the right rail = this agent.

## 设计决策

- Mounts as a normal `/app/you` sub-page → renders through MainLayout's
  `<Outlet>` overlay and inherits the close-X automatically ([[MainLayout]]).
- Three tabs map onto the brand spine: **Memory** (carbon · Narra),
  **Network** + **World** (silicon · Nexus). Header carries the
  carbon·silicon binding-dot motif.
- **Notes** is the human (carbon) half — a personal scratchpad, persisted to
  `localStorage['narranexus:you-notes:<userId>']`. A later change adds an
  explicit "save as memory" that writes a `memory_observation` the agents can
  read (closing the carbon→silicon loop).

## tabs & layout

A **search** box (right of the tab row, on ALL tabs) filters the active
visualization — its `query` is passed as `search` to [[NarraMemoryTimeline]] /
[[NexusNetworkGraph]] / [[WorldviewLenses]] and cleared on tab switch (a stale
query mustn't hide everything).

Tabs are **Narra Memory** (carbon), **Nexus Network**, **Worldview** (silicon).
Full-height flex column: header + tabs are fixed, the panel `flex-1` (the
visualization gets the bulk of the height), Notes docked compact at the very
bottom. Each tab owns its own layout — empty-state tabs center their content;
the Memory tab fills.

## 阶段 (phasing)

- **Narra Memory — WIRED**: renders [[NarraMemoryTimeline]]
  (`api.getMyNarratives` → `GET /api/me/narratives`, [[me]]).
- **Nexus Network — WIRED**: renders [[NexusNetworkGraph]]
  (`api.getMyNetwork` → `GET /api/me/network`, [[me]]).
- **Worldview — WIRED**: renders [[WorldviewLenses]]
  (`api.getMyWorldview` → `GET /api/me/worldview`, [[me]]). **No fabricated
  data is shown in product.**
- **Notes** — working local scratchpad (`localStorage`); a later change adds
  "save as memory" (writes a `memory_observation`).

## 新人易踩的坑

- Lazy-loaded in [[App]] (`lazy(() => import('@/pages/YouWorkspace'))`) — needs
  the `export default`.
- The page is purely owner-scoped; it must NOT depend on the currently
  selected `agentId`. It reads `configStore.userId / displayName` only.
