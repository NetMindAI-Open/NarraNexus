---
code_file: frontend/src/components/you/NarraMemoryTimeline.tsx
last_verified: 2026-06-23
stub: false
---

# NarraMemoryTimeline.tsx — the Narra Memory tab (real data)

## 为什么存在

The first real-data visualization of the "You" workspace ([[YouWorkspace]]).
Renders the user's lived storylines (narratives across ALL their agents) as a
timeline. Data source: `api.getMyNarratives()` → `GET /api/me/narratives`
([[me]]); seeded scaffold narratives are filtered out server-side.

## 设计决策

- One narrative = one lane: a **carbon** bar spanning `created_at → updated_at`
  on a shared time axis, gutter-labelled by storyline name + a **silicon**
  agent dot (carbon=Narra/you, silicon=the agent that holds it). Point-in-time
  narratives (created == updated, common early on) get a min-width marker so
  they stay visible. Axis = 4 short-date ticks + a carbon `now`.
- Click a lane → a detail card (topic_hint, summary, started/last-active,
  rounds, keywords). Pure client layout maths in a single `useMemo`.
- Optional `search` prop filters storylines by name / summary / topic / owning
  agent (driven by the [[YouWorkspace]] search box).
- Owner-scoped: it must NEVER read the selected `agentId`.

## 新人易踩的坑

- Backend returns ISO-UTC strings (or null); guard `Date.parse` (`ts()` helper)
  — don't assume a parseable value.
- When the user has zero lived narratives the component shows an empty state,
  NOT an error — that is the expected cold-start, storylines accrue as agents
  actually converse.
