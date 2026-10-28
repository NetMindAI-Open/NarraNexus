---
code_file: frontend/src/components/agents/AgentActivityCard.tsx
last_verified: 2026-08-27
stub: false
---

# AgentActivityCard.tsx — Profile 上唯一的时间维度

## 为什么存在

[[../../pages/AgentProfilePage.tsx]] 原本只回答「这个 agent 是什么」（
[[AgentOverviewCard.tsx]] 的框架/模型/当前任务）和「它手上有什么」（JobsPanel /
AgentInboxPanel），**没有任何东西回答「它最近怎么样」**——没有活动节奏、没有今日
成本、看不到谁正在跟它说话。

2026-08-27 Dashboard 的行内展开被拆掉后，`Sparkline` / `MetricsRow` /
`SessionSection` / `RecentFeed` 四个组件同时失去了唯一调用方。它们表达的正是上面
那个缺口，所以不是「把无家可归的组件塞个地方」，而是这四个刚好凑齐了 Profile 真正
缺的那一层。本组件是它们的容器，自身不含任何数据逻辑。

## 布局为什么是这个形状

- **上半分两栏**：左边 24h 节奏、右边今日总计。两者都是「一眼看完」的信息，并排
  比上下堆叠省一屏。`sm:` 以下堆叠——sparkline 需要整宽才读得出形状，不能在窄屏
  和指标抢一行。
- **下半在分隔线以下**：实时会话和最近事件是次要信息，需要时才看，不该和上半争
  视线。
- **卡片整体排在 OverviewCard 之后、Jobs/Inbox 之前**：比静态配置更即时，比具体
  条目更概括。测试 `AgentProfilePage.ui.test.tsx` 用 `compareDocumentPosition`
  钉住了这个顺序。

## Gotcha

- **次要区块的条件渲染是本组件唯一的真逻辑**。`SessionSection` 和 `RecentFeed`
  在数据为空时各自 `return null`，所以如果无条件渲染那个 `border-t` 容器，安静的
  agent 会在指标下面看到一条**光秃秃的 40px 空条**。容器必须自己判
  `hasSessions || hasEvents`——两个子组件都不知道对方存不存在，只有 wrapper 知道。
  `data-testid="agent-activity-detail"` 就是给这条断言用的。
- **调用方负责挡掉公开 agent**，本组件不判。它要求传入 `OwnedAgentStatus`（非空），
  而公开 agent 只有 `PublicAgentStatus`，页面那边 `ownedStatus` 是 `null` 就整块
  不渲染。这样本组件不必推理「别人的 agent」，类型上也不可能拿到它的字段。
- **首次进页面可能不渲染**，因为 `ownedStatus` 要等一次轮询。刻意不给骨架：闪一下
  骨架再换成内容，比晚半秒出现更吵。
- 六个孤儿组件里 **`QueueBar` 没有收进来**。设计稿原本要把它移进 JobsPanel 头部，
  实现时发现 [[../jobs/JobStatusMeter.tsx]]（同日新增）已经就是那个东西——比例条 +
  带计数的图例。塞进去等于把 JobStatusMeter 刚消灭的重复再造一遍。
  `QueueBar` 与 `JobsSection`（内容 JobsPanel 本来就覆盖）因此**已于同日删除**，
  连带 `dashboard.jobState` / `dashboard.queue` / `dashboard.jobs` 三棵 i18n 子树。
