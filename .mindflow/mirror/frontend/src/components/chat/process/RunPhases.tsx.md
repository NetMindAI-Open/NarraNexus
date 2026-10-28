---
code_file: frontend/src/components/chat/process/RunPhases.tsx
last_verified: 2026-08-31
stub: false
---

## 2026-08-31（二）— 计时器接上真实起点

新增可选 `startedAtMs`。此前从 mount 起算（与退役面板逐字相同，不是本次引入），
刷新后接上一个已跑 10 分钟的 run 会从 `00:00` 起跳。

**是本次让它变得刺眼的**：序章现在和 `ResumedRunChip` 同列相邻，而那个 chip
读的是 `resumedRun.startedAtMs` 的真实时长——两个时钟并排给出不同数字，比只有
一个还糟。[[../ChatPanel]] 只在 `resumedRun.runId === currentRunId` 时传这个锚点
（同一条守则：锚点只许标记它自己那一轮），新起的 run 传 undefined，回落 mount
时间——对新 run 而言两者本就是同一瞬间。


# RunPhases.tsx — 一次运行的序章

## 为什么存在

`segmentTurn` 只能呈现**模型产出的东西**，而后端在模型开口之前还有真实工作：
选叙事 → 加载模块 → 同步实例 → 构建上下文。从按下发送到第一句叙述上屏，
文档流里一个字都没有。这段窗口需要有人说话，这就是本组件。

## 为什么它不是一个面板

它取代的是 `ProcessPanel` 那个带边框的终端盒子（2026-08-31 退役）。Owner 在
验收文档流时直接问「为什么还留着一个过程框」——问得对：同一屏里，agent 的
turn 已经无框铺开，下面却坐着一个 `rounded-lg + border + nm-paper + shadow`
的小窗，**两种语域并存**，正是这一版要消灭的东西。

**信息全部留下，只去掉框**（铁律 #16）：相位行、ops 计数、计时都在，变成文稿
开头的轻量行。mono 字形与 `»`/✓/spinner 保留——它读起来像机器脚手架而不是
「agent 说的话」，而它确实就是脚手架。

## 刻意不做的事

- **不画过程事件**。叙述 / 工具行 / 推理由 [[../TurnTimeline]] 在流里渲染，
  这里再画一遍就是同一行印两次。测试里有一条反重复断言钉住。
- **不做折叠**。退役面板有折叠态（头部一行活动摘要），那是因为它是个占地方
  的盒子；序章只有几行，折叠控件比它要折叠的内容还重。折叠态那行「现在在
  干什么」的信息由流本身承担——流里正在发生的事就写在下面。
- **不接管 plan**。plan 要钉住不滚走，进了文稿就会滚上去，所以它单独去了
  [[PlanStrip]]。

## Gotcha

- **调用方的门不能依赖 `currentEvents`**。首版把本组件放在
  `isStreaming && currentEvents.length > 0` 里面，而 `currentEvents` 要等
  agent loop 才有第一行——正好把本组件要填的那段空白挡掉了。它必须在
  `isStreaming` 一成立就挂载。

- `phaseSettled` 必须喂**未过滤**的 steps：白名单之外的 run-agent / 收尾 id
  正是「后面的相位已开始」的证据。和 [[../team/TeamMemberPanel]] 同一条规则，
  改一处两边跟（铁律 #8）。
- `PHASE_STEP_IDS` 白名单同时挡住未映射 step 的**原始英文 title 泄漏**。
