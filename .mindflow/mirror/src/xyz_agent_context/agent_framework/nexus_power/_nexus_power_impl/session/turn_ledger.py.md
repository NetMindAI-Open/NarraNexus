---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/session/turn_ledger.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — tool_use_start 发「名字先行」ui 事件

`record_model_event` 的 `tool_use_start` 从「无账本状态、返回空」改为发一条
ui 轨 `TYPE_TOOL_USE_START`（payload = call_id + tool_name）。参数是流式生成
的，名字远早于调用完成就已知道——不发这条事件，前端只能等 tool_use（参数齐）
才知道在调什么，长参数流上是一段可见的空窗。不进账本状态：真相由随后的
tool_use 落定（同 call_id 覆盖语义在 adapter/前端实现）。

# session/turn_ledger — 回合唯一真相

三不变量构造保证(配对只能经 synthesize 收口/角色交替经步末折叠/seq 唯一分配)。step 文本+调用在 step_done 折叠成单条 assistant 消息(role 交替成立的机制)。compaction 登记 seq→消息替换,投影替换、日志留全史。resume=base 前缀续 seq。
