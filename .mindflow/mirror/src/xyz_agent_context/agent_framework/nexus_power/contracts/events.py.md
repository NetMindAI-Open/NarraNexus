---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/events.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — TYPE_TOOL_USE_START（ui 轨）

工具名已到、参数仍在流式生成。UI 用它立刻显示「正在用 X」；参数齐后
TYPE_TOOL_USE 携带完整参数按同 call_id 覆盖。仅 ui 轨，不参与上下文重建。

# contracts/events — 两轨事件与账本条目类型

track=model(重建上下文)/ui(仅前端重放)是恢复/回放/压缩一切能力的前提(C1)。entry 即 schema:LedgerEntry 与未来 nexus_events 行同形,落库换 writer 不改类。压缩是追加 TYPE_COMPACTION replacement 条目,不是删除。payload 形状用 TypedDict 声明(R2),金样测试锁定。Usage.as_legacy_dict 同时给出双词汇 cache 键(遗留消费两种拼法都探)。Phase 固定枚举是正式决策(R4),伪相位走 hook。
