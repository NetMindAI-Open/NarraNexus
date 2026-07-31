---
code_file: frontend/src/components/chat/ProcessPanel.tsx
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 阶段行 / 呼吸灯 / 活光标改用 processShared 共享件

行为零变化：同款 markup 移入 processShared（LiveDot / PhaseRow /
LiveCursorRow），本文件改为消费 —— team 成员终端卡与单聊自此共享
同一实现。

## 2026-07-30 (r4) — 渲染件抽到 process/processShared.tsx

PHASE_LABEL_KEYS / friendlyToolName / formatElapsed / deriveActivity /
Activity 和事件行渲染（∴/$/↳，现为 ProcessEventRows 组件）剪切到
[[processShared]]，本文件只保留面板外壳（折叠头、滚动跟随、plan 区）。
行为、样式、testid 零变化——抽取只为团队成员详情复用同一套终端渲染。

## 2026-07-30 (r3) — 收进 pipeline 阶段 + 可折叠（Owner 反馈第二轮）

- **pipeline 阶段行收进面板**：原消息区悬浮的「Starting up…/Loading
  context…」指示器删除，映射（step id → chat.execution.* label）整体
  搬进来，phases 作为 `✓/spinner` 行渲染在过程体顶部。因此面板挂载即
  渲染（空数据显示启动态），不再返回 null——旧的「空则 null」契约作废。
- **可折叠**：头部整条可点。折叠态 1-2 行：当前活动（最新 tool 名 $
  高亮 / thinking / 阶段 label）+ 计时；有 plan 才多一行 n/m 进度条 +
  当前 ▶ 步骤（process-plan-mini），没 plan 不渲染。
- **phaseDone 推导**：后端把早期阶段一直标 running 到回合结束，所以
  「后面的阶段已出现（或 loop 已有事件）」才是诚实的完成信号。
- `deriveActivity` 是模块级纯函数——写成组件内 useMemo 会触发 React
  Compiler 的 memoization-preservation 报错。

## 2026-07-30 (r2) — 终端化视觉升级（Owner 反馈：太单调）

第一版只有灰字滚动。r2 换成真终端语言，全部走主题 token（亮暗都成立）：

- **chrome 头部**：呼吸绿点（挂载即运行，不需额外状态）+ mono 大写标题
  + 右侧 ops 计数与逐秒计时（挂载即回合开始，计时即回合耗时）。
- **行符号分物种**：`∴` 思考（斜体 ink50）、`$` 工具（success 绿 + 工具名
  silicon 加粗，MCP 前缀剥掉与 TurnTimeline 同规则）、`↳` 输出（缩进
  ink50）；pending 行是 warning 色 spinner + 省略号。
- **行尾 `❯ ▌` 闪烁光标**：终端的「还在跑」心跳。
- **plan 底部**：n/m 计数 + 迷你进度条（success 填充），active 步 silicon
  加粗 ▶，完成步 ✓ 划线。

测试契约不变：tool-row-{id} / data-pending / process-plan / 空则 null /
最后一份 plan 快照。

# ProcessPanel.tsx — 运行中的 terminal 风格过程面板

## 为什么存在

过程和回复原先按时间顺序混在同一条 TurnTimeline 里，靠实线/虚线左框
分层。信息不缺，但读的人要在噪音里找答案。分开之后气泡只有答案，过程
在这里连续滚动——像 terminal 一样**可以扫，不必读**。

## 设计决策

- **只在运行中挂载**（ChatPanel `isStreaming` 时）；结束即卸载，过程
  按回复切段折叠回各自气泡（segmentTurn）。所以这里不做任何持久化。
- **plan 钉在底部、不参与滚动**：它回答「现在到哪了」，不该被滚走。
  取 events 里最后一条 plan（store 对 plan 已是原地替换，最后一条即
  最新快照）。
- **pending 工具行**：名字 + 转圈 + 省略号——「名字已定、参数还在写」
  的诚实可见形态。
- **滚动跟随**：默认贴底；用户一旦上滚（距底 >24px）就不再抢视口，
  和消息区同一套取舍。

## Gotcha

- 面板只渲染 process 事件（thinking/tool_call/tool_output）+ plan；
  reply/native_output 属于气泡，在这里画就是重复渲染。
- 无过程且无 plan 时返回 null——空面板一个像素都不该占。
