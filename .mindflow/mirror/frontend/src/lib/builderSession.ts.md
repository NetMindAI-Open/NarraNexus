---
code_file: frontend/src/lib/builderSession.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 — 多持久化一个 `visited` 标记

`nn.studioVisited.<agentId>`：进过 studio 且未按完成，用来在收起后仍提供 Builder tab 作为
再入口。与 flag 一样是 per-tab 的（换标签页后再入口消失）——接受，不是 bug，文件头已写明。

## 2026-09-03 (评审修订) — 降级为纯持久化层

评审 🟡#3 / #6 / #7 指向同一个根因：**sessionStorage 直读没有订阅者**。面板只在
name / awareness 变化时重渲染，所以只推荐 skill 的一轮面板什么都不出现；关抽屉
无法结束 studio（没人会因为 flag 变了而重渲染编码器）；`useStudioTurn` 收集的写
失败没有任何读者。

现在响应式真相在 [[../stores/studioStore.ts]]，本模块只剩三件事：启动时
`loadStudioSession()` 一次性把整个命名空间读出来、`persistStudioFlag` /
`persistRecommendations` 写透。**组件不再直接 import 本模块。** 原来的
`openStudio / closeStudio / isStudioOpen / readRecommendations /
saveRecommendations / clearRecommendations` 全部迁到 store（死导出
`clearRecommendations` 由 `closeStudio` 顺带覆盖 —— 离开 studio 即清推荐）。

# builderSession.ts — studio 开关 + 未落地的推荐

## 2026-09-03 — 从「一次性标记」改成「持久开关」

初版是 consume-once：只包裹第一条消息。改成结构化面板 + 实时落库之后这不成立
了 —— 指令必须**每轮**都带（信封里的当前配置才是让用户手改成为权威的东西），
而且弱模型被告知一次后过几轮就不再吐 `<agent_draft>` 块。所以读操作
`isStudioOpen` 是**只读的**，绝不消费。

## 为什么用 sessionStorage

不是路由 state，也不是 store 字段：比路由 state 活得久（刷新还在）、按标签页
隔离（两个 studio 不互相抢）、不用改 chatStore 那条承重的提交路径。

## 两块状态，边界很重要

| 存什么 | 存哪 | 为什么 |
|---|---|---|
| studio 开关 | 这里 | 纯 UI 状态，服务端不该知道 |
| **推荐**的 skills / channels | 这里 | **不是 agent 状态** —— 服务端没有「某个 agent 被建议用 web-search」这回事 |
| 名称 / 描述 / 指令 | **agent 上** | 直接写进去，服务端是它们的唯一真相，本模块绝不影子存一份 |

推荐要按 agent 存，是为了出站信封每轮能重述它们；不然模型每条回复都会把同一个
skill 再推荐一遍。

## 没有「放弃」

面板写的是真实 agent，用户离开时没有可回滚的东西。离开 studio 只是清掉开关。
