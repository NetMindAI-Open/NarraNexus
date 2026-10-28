---
code_file: frontend/src/types/migration.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `ApplyResult.summaries_degraded`

新增一个计数字段：本次导入里**没有走 LLM**、改用确定性 fallback 摘要的 session
数量。触发条件是用户在导入进行中按了「停止」（机制见
[[../../../src/xyz_agent_context/migration/hurry.py]]）。

**这个字段的意义就是「不许悄悄发生」。** 按停止之后剩余 session 的叙事会变薄
（标题 + 原文，而不是模型总结），如果不上报，用户拿到的是一份看不出区别的、
更差的导入结果。所以后端逐条计数、前端在
`lib/migrationImportQueue.ts` 里跨项目累加成 `summariesDegraded`，由 UI 明说。

类型上是**必填** `number`，所以少显示一处会在编译期暴露。但
`migrationImportQueue.ts` 的累加处仍写了 `?? 0` —— 那是对**旧后端**的兜底
（返回体里没有这个字段时不至于把整个和算成 `NaN`），不是类型可选的意思。改这个
契约时两边要一起看。

# types/migration.ts — Frontend types for Agent Migration

## Why it exists

TypeScript mirror of the Agent Migration standardized JSON contract
(`src/narranexus/platform/schema/migration_schema.py`) plus the `ApplyResult`
returned by `POST /api/migrate/apply`. Consumed by [[api]] (the migrate*
methods) and [[ImportAgentModal]].

## Gotchas

- Must stay in **lock-step** with the Python schema: `/scan` returns a
  `StandardizedAgentImport` that the UI POSTs back to `/apply` unchanged, so a
  drift between the two shapes silently drops fields on the write path.
- Re-exported through the `@/types` barrel ([[index]]).
