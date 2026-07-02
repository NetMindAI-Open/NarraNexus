---
code_file: frontend/src/components/you/WorldviewLenses.tsx
last_verified: 2026-06-24
stub: false
---

# WorldviewLenses.tsx — the Worldview tab (real data)

## 为什么存在

The third "You" workspace visualization ([[YouWorkspace]]): "your world" =
how each of your agents sees YOU + each agent's own worldview. Data:
`api.getMyWorldview()` → `GET /api/me/worldview` ([[me]]).

## 设计决策

- **Summary on top** + **one collapsed row per agent** (click → expand). The
  earlier all-expanded card grid got cluttered as agent count grew; collapsing
  keeps it scannable. The summary card frames it (binding dots + count + a
  clickable agent chip per lens that jumps to & opens that row). A collapsed
  row shows the agent + a truncated `sees_you`; expanded shows full **"sees you
  as"** (carbon — the agent's `persona`) + **"its worldview"** (silicon — a few
  `memory_observation subtype='world'` bullets). carbon = human half, silicon =
  agent half. `expanded` is a `Set<agent_id>` (multiple can be open).
- A real LLM-synthesised summary is a possible follow-up; for now the summary
  is honest framing + count (no fabricated consensus).
- **No synthetic consensus.** The original mockup showed agreed-on traits with
  agreement dots; deriving real consensus from free-text personas needs an LLM,
  so we show each lens's real words honestly instead.
- Optional `search` prop filters lenses by agent / view / worldview text.
- Owner-scoped: never reads the selected `agentId`.

## 新人易踩的坑

- The backend takes ONLY the agent's record where `entity_id == user_id` (the
  agent's view of YOU), not every `subtype='user'` entity (those include other
  people, e.g. 张继).
