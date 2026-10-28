---
code_file: frontend/src/components/agents/AgentTeamAvatars.tsx
last_verified: 2026-08-24
stub: false
---

# AgentTeamAvatars.tsx — Agent 所属团队的共享视觉入口

Dashboard 与 Agent Profile 必须用完全相同的 Team 表达，避免同一关系在不同页面
退化成 chip、文字或不同头像。本组件集中实现双色 `GroupAvatar`、多团队轻微重叠、
无团队 `—` 空状态，以及 hover/focus 可访问的 Team Profile Tooltip。

Tooltip 只消费 `teamsStore` 已有数据，展示团队全名、成员数量和 `description` →
`intro_md` → 本地化空状态的介绍降级链，不产生额外请求。头像点击会停止冒泡，
避免 Dashboard 行导航被误触；调用方仍负责字段标题或语义图标等页面级布局。

## 2026-08-25 — 新增反向组件 TeamMemberAvatars

[[TeamMemberAvatars.tsx]] 是本组件的镜像：本组件在 Agent 行上显示所属 Team 头像，
新组件在 Team 行上显示成员 Agent 头像。两者共享同一套"头像 + hover Profile"视觉
语言，但各自独立实现（Team Profile 卡 vs Agent Profile 卡的字段完全不同），彼此
不复用代码。

