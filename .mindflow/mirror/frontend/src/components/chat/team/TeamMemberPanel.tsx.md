---
code_file: frontend/src/components/chat/team/TeamMemberPanel.tsx
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 (二次) — 观察通道 fatal 错误的诚实展示

live 成员 + observation.errorMessage + 零事件 → 显示 detailLoadFailed，
不再挂着永远兑现不了的 "Starting up…"（配合 hook 的 fatal 停梯）。

# TeamMemberPanel.tsx — 成员详情 = 迷你 ProcessPanel

## 为什么存在

2026-07-31 Owner 反馈：roster 展开后的运行视图（灰点素行的
PhaseTimeline）和单聊 ProcessPanel 是两个世界，「预期是 chat 那个
ProcessPanel 差不多精致的」。本组件把成员详情做成同一张终端卡：
chrome 头条（LiveDot 呼吸灯 + `name · process` + ops/elapsed）、
`✓`/spinner 阶段行、`$` 工具行、`❯▌` 活光标、follow-scroll、plan
页脚 —— 全部复用 processShared 的共享件，与单聊同一套字形语言。

## 数据源（两层，且不是装饰性区分）

- **running/stalled** → [[useRunObservation]](activity.event_id)：
  真·实时 thinking/tool 流（bus 活动行运行中就带 event_id，#199 系列
  铺的钥匙）。观察 socket 连上前（~0-1s）显示诚实的 "Starting up…"
  兜底，不假装知道更多。
- **idle** → useTurnDetail 的持久化 event_log + TurnTimeline 渲染器
  （2026-07-31 已拍板的 reasoning & tools 视图），活在 430px 宽里。
  上一 turn 的结果绝不显示在当前 turn 下（settled key 校验，沿袭
  v1 语义）。

## Gotcha

- `open=false` 时返回 null 且 observation `enabled=false` —— 折叠行
  零 socket。
- stalled 成员观察照常工作（tail-follow 一直跟到心跳死），卡片底部
  额外亮 amber 的 silentFor 行。
- elapsed 用父级共享 `now` 时钟 + poll 的 started_at —— 屏上所有
  时长同一口径（v1 决策沿袭）。
