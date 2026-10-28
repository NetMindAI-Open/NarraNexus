---
code_file: frontend/src/stores/studioStore.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 (评审二轮) — 「收起」与「结束」分开；studio 可恢复

评审 🟡#12：上一轮把关抽屉接成出口后，抽屉 X 成了一个没标签、不确认、不可逆的「结束 AI
创建流程」按钮，还顺手把推荐删了盘，且 `openStudio` 只在建 agent 时调用——一次误触永远
回不来。现在：

- `closeStudio` = **收起**：清 flag 与 error，**保留** `recommendations` 与新增的
  `visited`（「进过 studio 且没按完成」）。`selectStudioResumable` = visited && !open。
- `finishStudio` = **结束**（面板「完成」）：flag / visited / recommendations / error 全清。
- 何时算「收起」不在这里判，也不在每个关闭入口判，统一在
  [[../hooks/useStudioLifecycle.ts]] 一个对账 effect 里。
- 上一条里「顺带让死导出 `clearRecommendations` 有了唯一真实调用」作废：推荐只在
  `finishStudio` 时清。

# studioStore.ts — 创建工作室的响应式状态（按 agent）

## 为什么存在

PR #382 评审的 🟡#3 / #6 / #7 三条指向同一个根因：studio 的开关和推荐存在
sessionStorage 里、**渲染期直读**，没有订阅者。于是：

- 只推荐 skill、不改文本的一轮，面板不重渲染，「来自对话」一节永远不出现；
- 关抽屉无法结束 studio —— 没有任何东西会因 flag 变化而重渲染 ChatPanel 的编码器；
- `useStudioTurn` 收集的写失败存进一个没人读的 state。

一个 zustand store 三件事一起收口：`open[agentId]`、`recommendations[agentId]`、
`applyError[agentId]`。[[../lib/builderSession.ts]] 降级为持久化后端：创建 store 时
`loadStudioSession()` 一次性水合，每次写透。

## 关键决策

- **flag 是持久开关，读不消费。** 指令必须每轮都带（信封里的当前配置才是让用户
  手改成为权威的东西），弱模型被告知一次后几轮就不再吐草稿块（铁律 #15）。
- ~~`closeStudio` 连推荐和错误一起清~~ —— 见 09-04 条，已改为只清 flag 与 error。
- **selector 返回稳定的空对象** `EMPTY_RECOMMENDATIONS`，避免没有推荐的 agent 每
  次 selector 都造新引用触发重渲染。
- `isStudioOpen(agentId)` 是给回调用的非 hook 读法（`getState()`），
  [[../hooks/useStudioTurn.ts]] 的 encode / apply 都在 render 外判定。

## 上游 / 下游

写：[[../pages/ChooseCreateMethodPage.tsx]]（open）、
[[../components/builder/BuilderConfigPanel.tsx]]（close via Done）、
[[../components/layout/MainLayout.tsx]]（close via 关抽屉）、
[[../hooks/useStudioTurn.ts]]（推荐 / 错误）。
读：以上全部 + [[../components/bookmarks/BookmarkPanelHost.tsx]]、
[[../components/layout/CommandPalette.tsx]]。
