---
code_file: src/xyz_agent_context/module/general_memory_module/general_memory_module.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4b：召回记忆整体搬进 get_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

此前 `get_instructions` 全文 = 召回记忆列表（逐条含 `(YYYY-MM-DD HH:MM)`
时间戳）→ 每轮必变，是 prod SYSPROMPT-BREAKDOWN 0/17 稳定的两个根因之一。
现拆为：

- `_render_recalled_memories(ctx_data)` — 原渲染逻辑原样提取（文案零改动，
  空列表 → ""）。
- `get_instructions` — flag 开 → 恒定常量 `GENERAL_MEMORY_STATIC_INSTRUCTIONS`
  （"## What you remember" 头 + 记忆在 turn context 的指引；**无记忆轮也返回
  同一句**，顺带消除了旧 "空轮返回 ''" 造成的模块小节体积 0/非 0 跳动这个
  次级不稳定源）；flag 关 → `_render_recalled_memories`（legacy 字节一致）。
- `get_turn_context` — 返回 `_render_recalled_memories` 输出（搬迁不裁剪，
  "trust the most recent" 指引随行）。

recall hook（hook_data_gathering / limit / token 预算）一行未动。

`_extract_facts` 里的 `OpenAIAgentsSDK().llm_function(...)` 改为
`get_helper_sdk().llm_function(...)`。意图同 engine.py：observation 抽取这个
helper_llm 调用点不再硬绑 OpenAI 协议，由 factory 按当前 task 的 helper 配置
分派 Anthropic/OpenAI 实现（铁律 #9），让单一 Claude key 可同时服务 agent 与
helper。调用签名与行为不变。

## 2026-06-08 — passive injection uses passive_kinds()

`hook_data_gathering` now recalls over `passive_kinds()` (observation/entity/narrative — distilled knowledge) instead of all kinds, so chat/event/job/bus no longer pollute the per-turn passive injection. They stay explicitly searchable via the `remember` tool. See [[spec]] / [[specs]].

# general_memory_module.py — unified Agent Memory

GeneralMemoryModule — learns world/experience observations each turn (hook_after_event) and injects cross-kind unified memory into context (hook_data_gathering via remember). The single point unified memory feeds the agent loop.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

**Timestamps as interim supersession (2026-06-05).** A memory about the same
thing changes over time, but the real update/supersession path is deferred
(too complex for now). As a stopgap, each injected memory line carries a
`(YYYY-MM-DD HH:MM)` stamp (`_recalled_at`: `record.updated_at or created_at`,
UTC) and `get_instructions` tells the agent to trust the most recent when two
memories disagree. This is a SOFT, render-time hint for the LLM — not hard
dedup: observations stay append-only, so conflicting versions can both surface
in recall and the agent picks the latest by timestamp. The timestamps already
existed on `MemoryRecord` (created_at column DEFAULT); this change only
surfaces them in the passive-injection path (the `remember`/`grep_memory`
tools already returned `when`).
