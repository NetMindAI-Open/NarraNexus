---
code_file: frontend/src/components/you/__tests__/narraMemoryLayout.test.ts
last_verified: 2026-08-06
stub: false
---

# narraMemoryLayout.test.ts — 时间轴几何的钉子

钉住 [[narraMemoryLayout]] 的四类修复（Base recvoAmUUSjKXs）：新近创建的
窄条右缘 ≤100、未来时间戳被 clamp、全解析失败兜底不越界、min-width 仍然
保证；短跨度刻度带时分（4 个标签互异）而长跨度维持日粒度；search 过滤/
created_at 升序/空集 null 的既有语义不回归。`now` 固定注入，测试确定性。
