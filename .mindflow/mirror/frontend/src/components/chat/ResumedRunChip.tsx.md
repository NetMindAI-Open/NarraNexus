---
code_file: frontend/src/components/chat/ResumedRunChip.tsx
last_verified: 2026-08-21
stub: false
---

# ResumedRunChip.tsx — 「已续接进行中的任务」徽标

## 为什么存在

深圳复测 B1:刷新中途的页面自动重连,后端从 seq 0 全量重放 event_stream,
而 UI 上没有任何「这是同一个 run 在继续」的信号——测试者把 610 秒 run 的
三次刷新重放记成「重新开始生成用时 10min」。这个 chip 把直播块锚到 run 的
**真实开始时间**(`chatStore.resumedRun.startedAtMs`,源头是 run_reconnect
帧的 `started_at`),渲染「已续接 · 已运行 N 分」。

## 上下游关系

**被谁用**:[[ChatPanel]] 直播块顶部,`resumedRun` 非空时渲染。
**数据来源**:[[../../stores/chatStore.ts]] `resumedRun`,由
[[../../services/wsManager.ts]] run_reconnect 分支写入(naive-UTC 解析坑
在那边处理,本组件只拿 epoch ms)。

## 设计决策 / Gotcha

- **纯呈现层**(铁律 #16):重放的帧、序、内容零改动,只加身份标注。
- elapsed 每 30s 自 tick(流式安静期——长工具调用——也不会停在旧值);
  `Math.max(1, …)` 钳制:亚分钟或时钟偏差(锚点在未来)永不显示 0/负数。
- 组件自带 interval 而不是依赖父级流式重渲——直播块的重渲染频率取决于
  帧到达,不可依赖。
