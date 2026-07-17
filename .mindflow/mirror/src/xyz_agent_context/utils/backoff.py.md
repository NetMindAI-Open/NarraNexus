---
code_file: src/xyz_agent_context/utils/backoff.py
last_verified: 2026-07-13
stub: false
---
# backoff.py — 共享指数退避公式

## 为什么存在

失败冷却的退避公式 `min(base·2^(n-1), cap)` 原本内嵌在 `job_module/job_trigger.py`
里。实时层 Agent 熔断器（`agent_framework/agent_circuit_breaker.py`）需要同一套退避，
为避免两处漂移，把纯公式抽到这里做单一来源。

## 上下游关系

被 `agent_circuit_breaker.compute_cooldown_seconds` 调用来算 COOLING 的 `cooldown_until`。
Job 层**仍保留**自己的内联副本（熔断器 plan 的范围决策 #2：不迁移 Job 层，只共享公式给
新代码），但本模块是今后的归属地。

## 设计决策

纯算术：无 I/O、无状态。`consecutive_failures < 1` 归一到 1，保证首次失败拿到 `base`
而不是 0。它只给"已经结束且失败的东西"间隔重试，绝不给运行中的 loop 设上限（铁律 #14）。
