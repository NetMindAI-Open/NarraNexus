---
code_file: frontend/src/components/layout/AgentGroupSection.tsx
last_verified: 2026-06-10
stub: false
---

# AgentGroupSection.tsx — One collapsible team section in the grouped sidebar

## 为什么存在

The 2026-06-10 sidebar redesign replaced the TeamFilterBar chip filter
with grouped sections (spec §11): the team is no longer a hidden filter
state above the list, it IS the list's structure. This component owns
one section: full-width header (disclosure triangle + team color dot +
name + member count) and the agent rows beneath it. Extracted from
AgentList so the list file stays orchestration-only.

## 上下游关系

- **被谁用**: `AgentList` (one instance per group from `buildAgentGroups`).
- **依赖谁**: `AgentRowMenu` (kebab), `agentGroupUtils.aggregateSectionUnread`,
  `RingAvatar` (nm), `AgentInfo` from `@/types`. All mutations (rename /
  delete / toggle-public / select) are callbacks owned by AgentList.

## 设计决策

- **Header is typography, not a chip** (spec design principle #1):
  full-width row, so team-name length never changes shape — this is
  what killed the "ragged chip cloud" complaint.
- Collapsed section shows an aggregated unread pill in the header —
  collapsing must not hide information (iron rule #16 spirit).
- `hideHeader` covers the pure no-teams scenario: a single Ungrouped
  header with nothing to contrast against is noise, so AgentList
  renders one headerless section (rows always visible — a headerless
  section cannot be collapsed).
- Hover-visible `→` on named team headers navigates to team detail —
  replaces the old undiscoverable double-click on chips. Ungrouped has
  no detail page, hence no arrow.
- `isOwner` is derived per-row from `agent.created_by === currentUserId`
  (threaded from AgentList) and gates delete / public-toggle in the
  kebab. The read-only Globe badge for OTHER users' public agents stays
  inline per the SHOW_AGENT_PUBLIC_TOGGLE flag contract (see
  AgentList.tsx.md).
- `AvatarWithStreaming` is exported and reused by AgentList's collapsed
  avatar rail so both renderings keep the identical streaming-halo
  treatment.
- Row visual contract preserved from the pre-redesign AgentList row
  (see AgentList.tsx.md 2026-05-19 entry): bg priority selected
  (--nm-row-active) > unread (--color-silicon-soft) > hover
  (--nm-paper-warm); preview = latest assistant reply; unread pill =
  transparent bg + ink30 hairline.

## 新人易踩的坑

Rename commit fires from both mouse (buttons) and keyboard
(Enter/Escape) — `onSaveEdit`/`onCancelEdit` are typed
`React.SyntheticEvent`, not MouseEvent. Don't narrow them back.
