---
code_file: frontend/src/components/teams/ClearTeamDataDialog.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 入口迁到房间的团队管理 tab

不再由 [[AgentList]] 渲染、也不再从 [[TeamRowMenu]] 的「清理数据」打开(两处都已删)。
现在唯一入口是 [[../chat/team/TeamManagePanel]] 的「清理团队数据…」按钮,确认后
`api.clearTeamData` → `onCleared(scopes)` 让 [[../chat/team/TeamChatPanel]] 丢掉对应内容。
组件本身与 scopes 语义未变。


# ClearTeamDataDialog.tsx — clear a team's chat / shared files

## Why it exists

Team counterpart to `ClearAgentDataDialog`. A team is a collaboration surface (group-chat
history + `_shared/teams/{id}` files); the owner needs a way to wipe that without deleting
the team. Two checkboxes — chat / files (chat defaults on, files opt-in) — map to
`api.clearTeamData(teamId, {chat, files})` → `DELETE /api/teams/{id}/data`. Danger-styled
confirm, disabled until a scope is picked.

## Upstream / downstream

- Rendered by [[AgentList]] (opened from the team row's [[TeamRowMenu]] ⋮ → "Clear data",
  mirroring how the agent clear-data dialog is opened from the agent row menu). AgentList
  owns the open/busy state (`clearTeamTarget` / `clearTeamBusy`).
- Backend: [[teams]] `_wipe_team_data` — keeps team, members, bus channel; deletes the
  room's `bus_messages` and/or the shared-files dir.

## 2026-08-10 — the files checkbox also takes the team's artifacts

Team artifacts are REQUIRED to live in `_shared/teams/{id}`, so deleting that
folder destroys their content and the backend cascades to their rows. The two
checkboxes are unchanged; what changed is what the second one MEANS.

This dialog is the only place a user is told, before pressing an irreversible
button, what it will do — so its copy (and the matching `optFilesDesc` strings)
is part of the behavior, not decoration around it. It had drifted: the cascade
was removed once and the copy updated to match, then re-introduced without the
copy being restored, leaving the dialog promising artifacts would be kept while
the server deleted them.
- i18n under `teams.clearData.*` (en+zh).

## 2026-08-11 — 第三个 scope：公告栏

新增一个**独立**勾选项，默认关。绝不并入 chat：公告栏之所以存在恰恰因为它不是聊天，
清 transcript 顺手删掉全部规则，会把用户送回复读循环。

文案明说「之后这些事需要重新告诉团队一次」。这一屏是用户按下不可撤销按钮前
唯一被告知的地方——上一轮的教训是文案与行为不一致，且那是自己的回归。