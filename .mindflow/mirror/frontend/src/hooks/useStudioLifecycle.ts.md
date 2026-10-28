---
code_file: frontend/src/hooks/useStudioLifecycle.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 (评审三轮) — 「完成」的收尾也归这里

`finish()` 不再请求抽屉 toggle（见 [[../components/builder/BuilderConfigPanel.tsx]] 09-04
三轮条），`finishStudio` 之后 agent 非 open 非 resumable，本 hook 的 `setDrawerTab(null)`
分支收起抽屉。`closeStudio(prev)` 加 `isStudioOpen(prev)` 守卫，已结束的 studio 不再白跑一次
set。测试补「finish 后抽屉收起且不可恢复」。

# useStudioLifecycle.ts — studio 活多久，只有这一条不变式

## 不变式

**studio 在 agent X 上打开 ⇔ 抽屉当前正为 X 显示 Builder 面板。** 一个 effect 对账，而不是
在每个能关抽屉的地方各判一次。

## 为什么

PR #382 评审 🟡#10：「离开 studio」曾只绑在抽屉 X 上，四条日常路径各漏一个方向——⌘K toggle
关掉同一面板、抽屉内切 tab、切 agent 再关抽屉（关的是当前 agent，原 agent 的 flag 留着）、
切 agent 不关抽屉（挂载守卫让 host 渲染空 div，抽屉却还开着、标题还是 Builder）。前三条的
后果：每条消息仍被包上指令 + 全量目录，模型继续以 Builder 身份往真实 agent 写配置，而面板
不在场，写失败又变回完全静默。以后任何新的关闭方式（Esc、手势、深链）都被构造性地覆盖，
因为这里没有枚举它们。

## 两个刻意的后果

- **抽屉内切到别的 tab 算收起。** Builder tab 仍在（agent 是 `visited`），回来一键、推荐还在。
  另一种选择（别的 tab 显示时 studio 仍活着）会让模型写入在没有面板承接错误的情况下发生——
  正是这一轮要消灭的静默。
- **选 Builder tab**：可恢复的 agent → `openStudio` 恢复；从未进过 studio 的 agent → 改成
  `setDrawerTab(null)`，抽屉永远不会开在一个空面板上。

## 边界

`prev` 首帧为 null 时不 close（避免首帧误触）。收起是 `closeStudio`（可恢复），只有面板
「完成」调 `finishStudio`；抽屉和本 hook 都不会结束一个 studio。
[[../components/builder/BuilderConfigPanel.tsx]] 的 `finish()` 先冲刷字段再 `finishStudio`，
**不碰抽屉**；本 hook 的 effect 随后看到「builder tab 上、非 open、非 resumable」即
`setDrawerTab(null)`。（三轮前这里写的次序是反的——面板先 toggle、hook 后 close——那正是
🔴#13 漏检的原因。）

## 上游 / 下游

唯一调用方 [[../components/layout/MainLayout.tsx]]；状态在 [[../stores/studioStore.ts]]；
可见性规则在 [[../components/bookmarks/tabs.ts]]。测试 `useStudioLifecycle.test.tsx`
覆盖六条路径。
