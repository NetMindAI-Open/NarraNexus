---
code_file: src/narranexus/platform/turn/inputs.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3a）— `TurnServices` / `StageInputs`（平台私有，alpha）

策略收到的是可变 `RunContext`（每个 step 已经认识的载体）+ 本回合的服务对象 + profile + silent 标记；冻结的
`contracts.agent.stages.*Context` 由 `observe.py` 在边界派生给钩子/快照。`aborted` 让 Ingress 的 LLM 配置
解析失败能像原来的 `return` 一样停掉流水线；`timings` 供 Commit 打 `[turn-timing]`。

## 2026-09-07 — TurnServices.bind_event

Optional callback the runtime supplies; the pipeline calls it once with the Event id the instant a stage creates the Event. Strategies never call it themselves.
