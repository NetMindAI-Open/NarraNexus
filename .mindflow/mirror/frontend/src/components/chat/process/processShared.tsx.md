---
code_file: frontend/src/components/chat/process/processShared.tsx
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 终端语言三件套沉淀（LiveDot / PhaseRow / LiveCursorRow）

ProcessPanel 的 ping 呼吸灯、`✓`/spinner 阶段行、`❯▌` 活光标抽成共享
组件：单聊面板与 team 的 [[TeamMemberPanel]] 同一套字形语言，改一处
两边跟（铁律 #8）。ProcessPanel 已切换为消费这三件。

# processShared.tsx — ProcessPanel 与团队成员详情共用的渲染件

## 为什么存在

单人聊天的 [[ProcessPanel]] 和团队房间成员栏的过程详情要长得一模一样——
同一套终端风格（∴ 思考 / $ 工具 / ↳ 输出、pending 琥珀 spinner、
friendly tool name 规则）。样式只能有一处事实源，否则两边会漂移。

## 内容物

- `ProcessEventRows`：TurnEvent[] → 终端行序列。纯渲染，无滚动无状态，
  过滤留给调用方；非 process 型事件返回 null。
- `deriveActivity` + `Activity`：折叠态"当前在干什么"一行的推导
  （最后一个 tool_call/thinking 赢，否则最后一个 pipeline 相位）。
- `PHASE_LABEL_KEYS` / `friendlyToolName` / `formatElapsed`。

## 上下游

消费者：ProcessPanel（单人 chat）、TeamRosterPanel 的成员详情（团队）。
输入契约是 TurnEvent 联合类型（types/index），历史回放侧由
`timelineToEvents` 归一到同一契约。
