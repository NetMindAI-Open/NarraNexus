---
code_file: frontend/src/components/teams/teamColors.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 团队配色单一来源

`COLOR_PRESETS`(8 色)从 [[TeamManagementModal]] 与 [[CreateTeamPage]] 的两份
拷贝收敛到此。design_system.md §6.2 豁免:团队 accent 是存进 `team.color` 的
**DATA**,不是 UI token,所以允许写死色值。数组顺序是契约——`[0]` 是新团队
默认色,增删只 append,别重排(存量团队的默认色会漂移)。
