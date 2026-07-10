---
code_file: src/xyz_agent_context/services/background_llm_alerts.py
last_verified: 2026-07-07
stub: false
---
# background_llm_alerts.py — 后台 LLM 失败告警

## 为什么存在

脱离任务（narrative updater、Step-5 entity/memory hooks）里的 LLM 失败以前是纯静默：
`logger.exception` 后 `return None`。2026-07 事故里平台 key 过期，这些路径 401 两周
无 owner 可见信号、也无可 SQL 的痕迹，长记忆无声退化。事故教训 #3（别吞异常）、#4
（L2 健康）、#5（DB 审计）都指向同一结论：后台 LLM 失败必须留可查记录，且当它是 owner
能修的凭据问题时，发一条去重的 owner 通知。

`alert_background_llm_failure(...)` 分两级（刻意）：
- **每次失败** → 写一条 `service_audit` error 行。永远开、便宜，运维几周后仍能
  `SELECT` 出"最近 N 天多少次后台 LLM 失败"，即使日志已轮转。
- **仅凭据类失败** → 写 owner inbox 通知（脱敏 + 冷却去重）。瞬时抖动（超时/5xx）
  owner 修不了，不进 inbox，避免告警疲劳。

`source` 短标签（narrative_update / entity_summary / memory_extraction /
post_turn_hooks）进审计 detail 和通知标题。冷却 map 在进程内（重启即清），与 message
bus 的失败通知同款权衡。函数**永不抛**——观察者不得破坏被观察者。

## 上游

被 narrative updater 的 `_async_llm_update` 和 AgentRuntime 的 `_run_hooks_background`
调用。复用 `agent_framework/llm_failure`（分类+脱敏）、`InboxRepository`（owner 通知）、
`ServiceAuditor`（审计）。
