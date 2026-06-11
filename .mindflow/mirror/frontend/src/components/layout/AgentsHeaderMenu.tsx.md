---
code_file: frontend/src/components/layout/AgentsHeaderMenu.tsx
last_verified: 2026-06-10
stub: false
---

# AgentsHeaderMenu.tsx — ⋯ overflow menu on the AGENTS section header

## 为什么存在

The retired TeamFilterBar crowded five interactions into the sidebar
top (Import / Export / Manage-teams icon buttons + chip click + chip
double-click). Spec §11.2 collapses the three low-frequency actions
into a single ⋯ overflow menu on the `[ AGENTS ]` header, leaving
`+` (create agent) and Refresh as the only always-visible actions.

## 上下游关系

- **被谁用**: `AgentList` sticky header.
- **依赖谁**: lucide icons; callbacks owned by AgentList
  (navigate to bundle import/export, open TeamManagementModal).

## 设计决策

- This is now the SINGLE entry point for team management. The old
  dual-entry design (TeamFilterBar gear + AgentList Users2 button,
  see AgentList.tsx.md 2026-05-09 entry) existed because the gear
  was undiscoverable; with the filter bar gone the duplication is
  no longer needed.
- Export no longer pre-fills a team scope (the old chip-filter
  context is gone). Per-team export lives on TeamDetailPage, reachable
  via the section header's → arrow.

## 新人易踩的坑

If you add an entry here, ask whether it's low-frequency enough —
this menu exists to keep the header at exactly two visible buttons.
