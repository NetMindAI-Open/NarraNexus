---
code_file: frontend/src/components/layout/agentGroupUtils.ts
last_verified: 2026-07-17
stub: false
---

# agentGroupUtils.ts — Pure grouping logic for the team-grouped sidebar

## 为什么存在

The 2026-06-10 sidebar redesign (spec §11, "chip filter → grouped
sections") needs to derive `team sections + Ungrouped tail` from
`agents × teams` in three places: the expanded grouped list, the
collapsed avatar rail (hairline dividers between groups), and tests.
Extracting the derivation into pure functions keeps `AgentList`
render-only and makes the grouping rules testable without rendering.

## 上下游关系

- **被谁用**: `AgentList` (expanded list + collapsed rail),
  `AgentGroupSection` (aggregateSectionUnread), layout tests.
- **依赖谁**: nothing but localStorage (collapse persistence).

## 设计决策

- `buildAgentGroups` returns teams in `teamsStore` order, then a final
  `teamId: null` Ungrouped group. **Multi-team agents appear in every
  team they belong to** (Owner decision, Slack-channel model) —
  selection highlights all copies because rows key on `agent_id`.
- Empty teams still produce a group (header renders with count 0) so
  users see the team exists; the **empty Ungrouped group is the only
  one callers skip**.
- Collapse state is a `Record<teamKey, boolean>` under localStorage
  key `sidebar_team_collapsed_v1`; Ungrouped uses sentinel key
  `__ungrouped__`. Read once on mount (`getCollapsedState`), written
  per-toggle (`setCollapsedState`).
- `aggregateSectionUnread` is injected with a `(agentId) => unread`
  getter rather than reading stores — keeps it pure and lets
  AgentList's `getRowMeta` stay the single source of unread truth.
- `sortAgentsByActivity` (added 2026-07-17) orders the flat AGENTS
  list so the most-recently-active conversation floats to the top
  ("recently chatted agent auto-pins"). Activity = newest of three
  signals: server `last_assistant_at`, a `localActivityMs(agent_id)`
  callback (freshest local session message, possibly not yet
  persisted — this is what makes an agent jump the instant you talk
  to it), and `created_at` as a floor so never-chatted agents still
  order by creation recency instead of collapsing to epoch 0. Same
  injection philosophy as `aggregateSectionUnread`: the local-time
  source is a callback, keeping the function pure. Stable by
  `agent_id` tie-break so equal-timestamp order never churns between
  renders. The backend `/api/auth/agents` applies the same rule
  (minus local sessions) as a pre-hydration baseline.
  Implemented with decorate-sort-undecorate: `score()` (which scans
  an agent's whole message list via the injected callback) runs once
  per agent, not the ~2·n·log₂n times an inline comparator would.
  This sort sits on the streaming path and message lists are
  unbounded in long sessions, so the single-pass cost matters.

## 新人易踩的坑

A multi-team agent's unread counts into EVERY section it appears in;
section aggregates are therefore not globally additive. That's
intentional — each header answers "is there activity in here?".
