---
code_file: frontend/src/lib/isBlankText.ts
last_verified: 2026-08-04
stub: false
---

# isBlankText.ts — 空白文本单一谓词

「空白=无回复」这条线跨四层（extract/persist/session/timeline），前端
两个消费方（chatStore 回复提取、buildTimeline 历史行过滤）共用本谓词，
防止判定写法在调用点之间漂移（review Minor #5 的收敛点）。一行实现，
不值得更重的抽象。
