---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/events.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（插件平台批 1）— 事件常量改从 `narranexus.contracts.agent_events` import

`agent_framework/loop/events.py` 已删除（无兼容垫片，铁律 #2），本文件对事件字典常量/构造器的
引用全部指向契约包；语义与线上值不变（`tests/snapshots/golden/agent_events.json` 钉住）。

## 2026-07-30 — TYPE_TOOL_USE_START（ui 轨）

工具名已到、参数仍在流式生成。UI 用它立刻显示「正在用 X」；参数齐后
TYPE_TOOL_USE 携带完整参数按同 call_id 覆盖。仅 ui 轨，不参与上下文重建。

# contracts/events — 两轨事件与账本条目类型

track=model(重建上下文)/ui(仅前端重放)是恢复/回放/压缩一切能力的前提(C1)。entry 即 schema:LedgerEntry 与未来 nexus_events 行同形,落库换 writer 不改类。压缩是追加 TYPE_COMPACTION replacement 条目,不是删除。payload 形状用 TypedDict 声明(R2),金样测试锁定。Usage.as_legacy_dict 同时给出双词汇 cache 键(遗留消费两种拼法都探)。Phase 固定枚举是正式决策(R4),伪相位走 hook。

## 2026-08-23(补)— TYPE_STEER_CONSUMED

新增 `TYPE_STEER_CONSUMED="steer_consumed"`(进 `VALID_EVENT_TYPES`):loop drain 后报「消费了哪些 steer_inbox 行」的
瞬态控制信号(非 ledger 行)。走独立 transport 行、被 driver 拦截,不需要 legacy adapter 翻译(见 [[runner.py]]/[[nexus_agent.py]])。

## 2026-08-24(补2)— TYPE_STEER_CONSUMED 不进 VALID_EVENT_TYPES

`TYPE_STEER_CONSUMED` 刻意**不**登记进 `VALID_EVENT_TYPES`:它走独立 transport 行、被 driver 拦截,从不作为普通事件
被校验或翻译;登记进「合法事件类型」会与它的 own-line/intercept 设计自相矛盾。
