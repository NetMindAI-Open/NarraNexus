---
code_file: frontend/src/pages/CreateTeamPage.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 色板去重、命名修正、键盘可达

`COLOR_PRESETS` 改 import 自 [[teamColors]](与 TeamManagementModal 同源,
两入口色板从此不会漂移);`sortedAgents` 改名 `matchedAgents`(它只 filter
不排序,旧名让人去找不存在的排序规则);成员勾选的假 checkbox 加
`role="checkbox"`/`aria-checked`/`tabIndex`/Space+Enter,键盘用户可勾选。

## 2026-08-06 (2) — 成员搜索 + 固定底部操作条(UI/UX 设计文档采纳项)

- Add agents 清单上方加搜索框(按 name/id 过滤;过滤不丢已勾选 —
  members Set 独立于视图)。
- 页面改 flex 列:字段区滚动,底部 shrink-0 操作条常驻(Cancel +
  Create team)— 修复原按钮沉在清单下方且无取消路径的问题;
  manageHint 移入操作条左侧。

# CreateTeamPage — v4 团队创建独立页

## 为什么存在

Chat UI v4 把「创建团队」从 TeamManagementModal 的左栏提升为整页
(侧栏 New 菜单入口):name / description / color 色板(与 modal 同一组
COLOR_PRESETS,视觉词汇一致)/ agent 多选清单 / Create。管理既有团队
(重命名、intro、成员、删除)不在这里 — 页脚提示去 Dashboard Teams 视图。

## 设计决策

- **成员选择延迟提交**:勾选只改本地 Set;Create 时先
  teamsStore.createTeam({name, description, color})拿 team_id,再逐个
  addMember(逐行 best-effort,失败聚合进 useNotice 弹窗 — 绝不
  window.alert,wry 会吞掉)。这修正了 modal 每次点击立刻打 API 的模式。
- description 首次真正随建团发送(modal 从来只发 {name, color})。
- 成功后直接 navigate 进新团队的群聊,让结果立即可见。
