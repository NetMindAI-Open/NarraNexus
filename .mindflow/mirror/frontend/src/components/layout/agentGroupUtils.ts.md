---
code_file: frontend/src/components/layout/agentGroupUtils.ts
last_verified: 2026-06-10
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

## 新人易踩的坑

A multi-team agent's unread counts into EVERY section it appears in;
section aggregates are therefore not globally additive. That's
intentional — each header answers "is there activity in here?".
