---
code_file: frontend/src/hooks/useStudioTurn.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 (六轮) — 渠道建议也不等目录

`channels` 只按编译期 `SUPPORTED_CHANNELS` 过滤，与目录无关，随文本字段那一段立刻写入；
`setRecommendations` 是整体覆盖，这一段的 `skill_ids` 原样带回 `prev.skill_ids`，不然会把
此前几轮接受的 skill 建议清空。目录落地后第二段只覆写 `skill_ids`。测试断言目录永不 settle 时
渠道建议在 `applyLiveFields` 之后立刻可读。

## 2026-09-04 (评审五轮) — 文本写入不再等目录

`applyFromReply` 先 `readCurrentConfig` → `mergeAgentDraft(prev, parsed, null)` → 立刻
`applyLiveFields`（name / description / awareness 不依赖目录），目录落地后再单独算
`skill_ids / channels` 写 `setRecommendations`。原来文本写入跟着目录一起等最多 12s，`prev`
快照在这期间过期，用户刚 blur 落库的手改会被按旧 `prev` 的 diff 覆盖。目录未知时推荐
仍整体回落（`builderProtocol.test` 钉着）。测试推进量改为从 `MARKETPLACE_SEARCH_TIMEOUT_MS`
派生，不留第三份数字。

## 2026-09-04 (评审四轮) — 超时下沉到请求本身

评审 🟡#17：`withTimeout` 只是不再等，底层 fetch 仍挂着，且 `.finally` 释放槽后下一轮又发
一个——marketplace stall 时每轮泄漏一个永不返回的 XHR，HTTP/1.1 的 localhost 6 连接占满后
整个 app 的请求排队。现在 `api.searchMarketplaceSkills` 自带
`AbortSignal.timeout(MARKETPLACE_SEARCH_TIMEOUT_MS)`（照 `setTeamPatrol` 的先例），abort 走
既有的失败路径；`withTimeout` 保留为第二道兜底，`CATALOGUE_TIMEOUT_MS` 由那个常量派生而
不是第二份数字（常量放 [[../lib/apiTimeouts.ts]]，因为测试整体 mock `@/lib/api`）。
失败 / 超时各记一行 `console.warn`——此前目录请求失败零诊断信号。

## 2026-09-04 (评审三轮) — apply 侧等目录有上限

评审 🟡#14：in-flight 去重 + apply 侧 `await loadCatalogue()` 合起来的新失效：第一次请求
挂死（不 settle、无超时）→ 那个 promise 永不清空 → 此后每一次 `applyFromReply` 都等它，
整个 session 的模型写入静默失效。`CATALOGUE_TIMEOUT_MS` + `withTimeout`（四轮起请求本身也会 abort）：超时视为未知、
释放 in-flight、下一次重试。刻意**有上限地等**而不是不等：`mergeAgentDraft(…, null)` 会让
首轮 skill 建议整体回落。测试用 fake timers 钉住「永不 settle 时 apply 仍在上限内完成」。

## 2026-09-04 (评审二轮) — 目录请求移出发送路径 + in-flight 去重

评审 🟡#11：上一轮把「目录未知就重试」放进了 `encodeOutgoing` 的 `await`，等于把 marketplace
的可用性挂到 Enter 键上——它挂着的时候输入框不清、气泡不出、`isLoading` 还是 false，再按
一次 Enter 同一句话发两遍（铁律 #16）。现在 `encodeOutgoing` 只读 `catalogueRef`（信封写明
unavailable、merge 侧整体回落），`void loadCatalogue()` 供下一轮；`applyFromReply` 不在发送
路径上，仍可 await。`loadCatalogue` 用 `catalogueInFlightRef` 保证同一时刻只有一个请求
（挂载 effect 与首条消息原本会并发打两个）。测试 `useStudioTurn.test.tsx`。

## 2026-09-03 (评审修订) — 目录「未知」+ 错误有出口 + 状态走 store

- **目录 `null` 直到拉到为止**。原来 fetch 失败置 `[]`、且 effect 只依赖
  `[studioOpen]` 不重试，于是一次 marketplace 抖动让本次会话每一轮都把推荐过滤成
  空并存盘。现在 `loadCatalogue()` 在 encode / apply 两条路径按需懒加载、失败保持
  未知并在下一轮再试；未知时 [[../lib/builderProtocol.ts]] 的 merge 让 `skill_ids`
  整体回落。「已加载且为空」仍拒掉所有 id。
- **写失败进 `studioStore.applyError[agentId]`**，[[../components/builder/BuilderConfigPanel.tsx]]
  与它自己的手改错误合成同一条提示行 —— `builderApply.ts` 注释里「surfaced in the
  panel」这句话现在是真的。不弹 modal、不禁用 composer（铁律 #15）。
- 推荐通过 `setRecommendations` 写 store，只推荐 skill 的一轮也能让面板重渲染，
  不需要无条件 `refreshAgents()`（那会让什么都没变的一轮多打两个请求）。
- 删掉从未被读的 `catalogue` state 与从未被消费的三个返回值；hook 只返回
  `{ encodeOutgoing, applyFromReply }`。`CATALOGUE_LIMIT` 是页大小不是静默上限：
  信封会告诉模型「first N of M」。

# useStudioTurn.ts — studio 插进普通聊天回合的两个点

## 为什么存在

[[ChatPanel.tsx]] 是 ~1400 行承重的流式逻辑。studio 只通过两个调用碰它 ——
发送前 `encodeOutgoing`，回合落定后 `applyFromReply`，其余全在这里。

## 关键决策

- **落定沿触发，不是监听消息列表**：`<agent_draft>` 块只有在回合结束后才完整，
  流式中途应用会把半序列化的 JSON 值写进 agent 的指令里。读的也是 store 里
  **已落定**的消息，不是流式缓冲。
- **skill 目录只在 studio 打开时拉**。它存在的唯一目的是让
  `mergeAgentDraft` 能拒掉不存在的 id，以及让面板能按 id 安装。拉失败时目录为
  **未知**（见上方 09-03 修订），不是「空」。
- **只刷新真正变了的东西**：identity 变了才 `refreshAgents`，awareness 变了才
  `refreshAwareness`。
- `encodeOutgoing` 任何失败都返回原文 —— studio 出问题不能把用户刚写的消息吞掉。

## 上游 / 下游

协议在 [[builderProtocol.ts]]，写入在 [[builderApply.ts]]，开关在
[[builderSession.ts]]，面板在 [[BuilderConfigPanel.tsx]]。
