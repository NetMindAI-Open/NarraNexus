---
code_file: frontend/src/components/agents/AgentOverviewCard.tsx
last_verified: 2026-08-25
stub: false
---

# AgentOverviewCard.tsx — Agent 的只读归属信息卡

## 为什么存在

`AgentProfilePage.tsx` 的 Overview tab 曾经把 Current Task / Jobs /
Inbox 摆成三张独立 `PaperCard`,视觉上像可点入口,实际都不可点;
Framework / Model 又挂在页头 meta 行里跟 team / last-active 混排。这个
组件把五者 + 新增的 Skills / MCP 摘要合并成一张卡,统一表达"这是关于
这个 agent 的只读事实,不是操作入口"——整卡没有 hover / cursor-pointer
/ onClick,跟下面真正可点击的 `JobsPanel`/`AgentInboxPanel` 形成对比。

**视觉方向经过两轮修正**:第一版用无边框圆角 tile(浅色底填充)分隔每个
信息块,这是三轮视觉稿迭代后 Owner 确认的方向(见
`reference/self_notebook/specs/2026-08-25-agent-profile-overview-card-
design.md`)。但 Owner 实际看到渲染效果后反馈这些填充色块太重("大黑
块"),要求改仿另一张卡片(Agent 详情侧栏)的样式——**扁平、无填充,
靠 hairline 分隔的 label/value 行**。现状:`Row`(Framework/Model/
Current Task,`grid-cols-[120px_1fr]` 定宽标签列 + 图标+值)、
`StatBlock`(Jobs/Inbox,大数字+图标+名词,并排两列,参照量了参考卡
"近 30 天"统计块的排版,无填充无边框)、`Section`(Skills/MCP,标题+
计数同一行,下方 chip 列表或空态文案)。三组之间用
`border-t border-[var(--nm-hairline)]` 分隔,组内的 `Row` 用
`divide-y` 分隔——整卡除了外层 `PaperCard` 自带的描边,内部完全没有
填充色块。Jobs/Inbox 的 flag(运行中绿点 / 未读红底)只在"有 running
job"/"有未读"时才渲染。

## 数据边界

Framework/Model/当前任务/Jobs/Inbox 的值全部由 `AgentProfilePage.tsx`
算好后当 props 传入,这个组件不重新订阅 `usePreloadStore`/
`useDashboardStore`,避免出现第二套状态来源。组件内部只多订阅两个
读取:`useSkillsList(false)`(只统计启用的 skill)和新增的
`useMCPList()`(见 [[useMCP.ts]])。两者都通过 `useConfigStore()` 的
全局 `agentId` 隐式取当前 agent——这个耦合在 `AgentProfilePage.tsx`
mount effect 里的 `setAgentId(agentId)` 已经存在,不是这个组件新引入
的风险。

Skills/MCP chip 列表最多显示 4 个(`MAX_CHIPS` 常量,扁平样式下比第
一版的 3 个稍宽松,因为不再有填充 tile 的视觉压迫感),超出用 "+N
more" 纯文字收尾,避免列表无限撑高卡片。
