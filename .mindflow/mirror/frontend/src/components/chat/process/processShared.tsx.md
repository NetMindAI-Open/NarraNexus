---
code_file: frontend/src/components/chat/process/processShared.tsx
last_verified: 2026-08-30
stub: false
---

## 2026-08-31 — 共享方的名字变了

`ProcessPanel` 删除后，本文件的共享对象是 [[RunPhases]]（单聊序章）与
[[../team/TeamMemberPanel]]（团队成员卡）。`phaseSettled` / `PHASE_LABEL_KEYS` /
`PHASE_STEP_IDS` / `PhaseRow` 的规则一字未改，只是文档里的调用方改名。

`LiveCursorRow` 失去了单聊这个调用方（退役面板用过它），但 [[../team/TeamMemberPanel]]
仍在用（`observation.status !== 'ended'` 时挂在最后一行下面），所以不删。
单聊序章 [[RunPhases]] **刻意没有**接手这个光标：那是终端「还在跑」的心跳，
而文稿流的心跳是内容本身在长出来，再加一个闪烁块就是两处说同一件事。

## 2026-08-30（二）— `ProcessEventRows` 退役

过程改由 [[TurnTimeline]] 在消息流里渲染，直播 / 落定 / 观察三个面同一份
实现，本组件失去全部调用方，**直接删除**（铁律 #2：不留兼容壳）。它的
测试一并删除。

本文件剩下的是流里没有的东西：`PHASE_LABEL_KEYS` / `PHASE_STEP_IDS` 白名单、
`phaseSettled`、`friendlyToolName`、`formatElapsed`、
`LiveDot` / `PhaseRow` / `LiveCursorRow`。

它原来带的 `data-testid={`tool-row-${id}`}` + `data-pending` 是有用的断言
抓手，已在 [[TurnTimeline]] 的 `ToolCallBlock` 上补回——不是顺手加的，是
删掉它会让"文档流形状"这件事无法断言。

## 2026-08-30 — `∴` 行分两档：独白去斜体、提到 ink70

`ProcessEventRows` 的 thinking 行现在按 [[monologueTier]] 分档：独白
（`monologue=true` 且 [[uiStore]] 偏好开）**去掉 italic、色阶 ink50 → ink70**；
provider CoT 保持 italic ink50 的草稿纸观感。

**同一个 `∴` 字形、同一行结构、同一条轨** —— 分档是行内色调/字形的事，不是
新行类型。没有为它新造 glyph（会和 `»` 阶段行、`$` 工具行抢辨识度），也没有
动 `PHASE_STEP_IDS` 白名单：**独白不是相位**，不进那张表。

与 [[TurnTimeline]] 的关系是**同一条内容的两个生命周期阶段**，不是两处显示：
本面板只在 streaming 期间挂载，turn 结束后 ProcessPanel 卸载、过程随
[[segmentTurn]] 折进对应 reply 气泡由 TurnTimeline 渲染。两边都改，是为了
让这两个阶段看起来一致。

**偏好走 prop，不在本组件订阅 store**（review 第 2 轮定的）：
`ProcessEventRows({ process, showNarration })`，由面板级调用方
（当时是 `ProcessPanel` / [[TeamMemberPanel]]）经 [[useNarrationTier]] 读出后传进来。
第一版是组件自己 `useUIStore`，但这是个**被两个面板复用的共享渲染件**——
往里埋一个不在 props 上的输入，下一个复用它的面板就会遇到「events 传对了、
显示还是不对」。

**`showNarration` 刻意不给默认值**（review 第 3 轮）：第一版写了
`= true`，那等于把这条契约从**编译期强制**降成约定，而且默认方向还是反的
——忘了传 = 无视用户「关闭」，不是回落到安全侧。现在漏传由 tsc 当场抓住。

## 2026-08-26 — 阶段名对齐真实语义 + 新增 PHASE_STEP_IDS 白名单

`PHASE_LABEL_KEYS` 过去整体错位一格：step 1（叙事选择）显示「加载上下
文」、step 2（模块加载）显示「加载资源」、step 3（**Execute Agent
Loop**，其实已进 loop）显示「构建上下文」。现在按后端每步真实动作命名：
1=选择叙事 / 2=加载模块 / 2.5=同步实例 / 3=构建上下文 / **3.4=运行
Agent**（新增，对应后端把 step 3 拆成「构建上下文(3)」和「运行 Agent
(3.4)」两个相位——见 [[step_3_agent_loop]] 的 `PHASE_*_STEP`）。

新增导出 `PHASE_STEP_IDS`（**从 `PHASE_LABEL_KEYS` 的 keys 派生的 Set**，
单一事实源——加相位只改一处）作为**白名单**:只有这些顶层相位画成阶段
行。工具子步 `3.4.x`、`3.5` 最终思考回声、收尾 `4/5` 都不是「现在在干
嘛」，白名单同时杜绝未映射 step 的原始英文 title 泄漏。仅作成员判定,显示
顺序来自 `steps` 到达序、不是这个集合。

另抽出共享 `phaseSettled(phase, allSteps, hasProcessEvents)`:相位是否落定
（✓ vs spinner）的判据从两个面板各写一份收敛到这里（此前 `ProcessPanel`
读全量 steps、[[TeamMemberPanel]] 读过滤后的 phases，白名单生效后两者会
给不同答案）。**两侧都传未过滤的 steps**,让「后面的相位已出现」能看到
白名单之外的 run-agent/收尾 id。`parseFloat('3.4.1')===3.4`(第二个点截断)
故工具子步永不给 3.4 提前打勾——刻意如此,别改成按段比较。改一处两边跟
（铁律 #8）。

## 2026-07-31 — 终端语言三件套沉淀（LiveDot / PhaseRow / LiveCursorRow）

ProcessPanel 的 ping 呼吸灯、`✓`/spinner 阶段行、`❯▌` 活光标抽成共享
组件：单聊面板与 team 的 [[TeamMemberPanel]] 同一套字形语言，改一处
两边跟（铁律 #8）。ProcessPanel 已切换为消费这三件。

# processShared.tsx — ProcessPanel 与团队成员详情共用的渲染件

## 为什么存在

单人聊天的 [[RunPhases]] 和团队房间成员栏要共享同一套终端字形语言
（呼吸灯 / `✓`·spinner 相位行 / `❯▌` 活光标 / friendly tool name 规则）。
样式只能有一处事实源，否则两边会漂移。

**过程事件行本身已经不在这里了**（2026-08-30）：它们由 [[TurnTimeline]]
在消息流里渲染，直播 / 落定 / 观察三面同一份实现。本文件现在只剩相位与
共享字形。

## 内容物

- ~~`ProcessEventRows`~~ —— 2026-08-30 退役，见本文顶部那条。
- ~~`deriveActivity` + `Activity`~~ —— 2026-08-31 随过程框一起退役。它推导的是
  **折叠态**那一行，而过程内联进文档后不存在折叠态；零调用方，直接删（铁律 #2）。
  死导出留在共享文件里，下一个人有概率把它当现成能力接上去，顺带把一个已经
  不存在的概念（「折叠态活动行」）重新引进来。
- `PHASE_LABEL_KEYS` / `friendlyToolName` / `formatElapsed`。

## 上下游

消费者：ProcessPanel（单人 chat）、TeamRosterPanel 的成员详情（团队）。
输入契约是 TurnEvent 联合类型（types/index），历史回放侧由
`timelineToEvents` 归一到同一契约。
