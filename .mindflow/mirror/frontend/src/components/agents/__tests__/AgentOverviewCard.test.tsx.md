---
code_file: frontend/src/components/agents/__tests__/AgentOverviewCard.test.tsx
last_verified: 2026-08-25
stub: false
---

# AgentOverviewCard.test.tsx — 归属信息卡的条件渲染回归

用替身 `useSkillsList`/`useMCPList` + 同步 `t()` 验证卡片自己的展示
逻辑,不依赖 `AgentProfilePage.tsx`(那边只断言 props 传递正确,见
[[AgentProfilePage.ui.test.tsx]])。覆盖:Framework/Model/Current Task
三个 `Row` 的渲染;Jobs/Inbox 两个 `StatBlock` 的 flag 只在"有 running
job"/"有未读"时出现,数值变化时旧 flag 必须消失;Skills chip 列表在
超过 4 个(`MAX_CHIPS`)时截断并显示 "+N more";loading 态显示
spinner、空态显示对应文案;MCP chip 按 `connection_status` 着色,
`Section` 标题旁的计数只统计 `connected`。断言全部按 `data-testid`
定位到 `Row`/`StatBlock`/`Section` 容器,不依赖具体的 CSS 类名或视觉
样式,所以 2026-08-25 从填充 tile 改成扁平 hairline 行时这份测试本身
不用大改,只是 testId 从 `-chip`/`-tile` 重命名为 `-row`/`-stat`/
`-section`。
