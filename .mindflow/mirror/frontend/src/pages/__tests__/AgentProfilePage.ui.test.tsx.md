---
code_file: frontend/src/pages/__tests__/AgentProfilePage.ui.test.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 删除走 useAgentActions

新增用例 `Delete confirms, deletes through the shared agent actions and lands
on the Dashboard`：Header ⋯ → Delete → 确认弹窗 → `api.deleteAgent('agent-1')`
→ `chatState.clearAgent('agent-1')` → 落到 `/app/dashboard`。

为什么钉这条：删除 / 改名的逻辑已抽成 [[useAgentActions]]，由两个门共用——
档案页 Header 菜单和侧栏行菜单 [[AgentRowMenu]]（后者由
`layout/__tests__/agentListRowMenu.test.tsx` 守）。这里守档案页这一半：
确认后才删、清掉该 agent 的聊天状态、落地 Dashboard。改 `useAgentActions` 或
改档案页删除后的落地页时，两个测试文件都要过。

## 2026-08-27 — 补两条:活动带的位置,以及公开 agent 什么都不给

- **顺序断言**用 `compareDocumentPosition` 而不是查 DOM 索引:概览卡和活动卡之间
  以后可能插别的东西,「activity 在 summary 之后」才是要守的规则,「activity 是第
  N 个孩子」不是。
- **公开 agent 用例**临时把 fixture 的 `owned_by_viewer` 翻成 false,断言活动卡和
  横幅都不出现,`finally` 里还原。这条守的是隐私边界:两块读的字段全在
  `OwnedAgentStatus` 上,给别人的 agent 渲染就是在读他的私有状态。
  用 `try/finally` 是因为 `dashboardState` 是 `vi.hoisted` 出来的**共享可变对象**,
  漏了还原会污染同文件后续用例。

# AgentProfilePage.ui.test.tsx — Profile 信息架构回归

用窄 store/API/重面板替身验证 Profile 的产品边界:Overview 必须挂载
[[AgentOverviewCard.tsx]](此处浅替身,断言它拿到正确的
frameworkLabel/modelLabel/taskLabel/jobsCount/inboxCount props,不在
这个文件里重复验证卡片内部的图标对/flag/chip 逻辑——那部分覆盖在
`AgentOverviewCard.test.tsx` 里)并挂载真实的 Jobs/Inbox 面板边界;
Capabilities 只列出 Network、Memory、Skills、MCP、Channels,默认只打开
一个原子能力面板。Settings 必须位于顶层 Tab,并按 General、Awareness、
Model & Framework 组织;General 水合名称与描述,Awareness 仍挂载既有
原子面板。Header 还必须复用 Team 双色头像与悬浮介绍,并显示 Team 语义
图标——Framework/Model 2026-08-25 起从 Header 搬进了
[[AgentOverviewCard.tsx]],不再在 Header 断言里覆盖。配置卡片
(Settings → Model & Framework)不受影响,仍要求同时带字段语义图标与
品牌图标、展示有效配置并提供 Configure 动作。Tooltip primitive 在此
替换为同步测试壳,测试聚焦产品 payload 与样式契约,不重复覆盖 Radix 的
延迟挂载行为。
