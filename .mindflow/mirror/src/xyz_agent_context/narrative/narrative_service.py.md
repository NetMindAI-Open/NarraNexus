---
code_file: src/xyz_agent_context/narrative/narrative_service.py
last_verified: 2026-06-01
stub: false
---

## 2026-06-01 — embed a clean retrieval anchor, not the execution prompt

`select()` gained a `retrieval_anchor` param. `resolve_retrieval_text(anchor,
input_content)` (module-level) picks `anchor` when a trigger supplied one
(`[From <name>] <body>`), else falls back to raw `input_content`. The resulting
`query_text` is what gets embedded (query vector), passed to continuity
detection, handed to `retrieve_top_k(query=…)`, and stored as
`session.last_query`. Motivation: IM/bus triggers were embedding the full
execution prompt (history + sender profile + instruction boilerplate), which
diluted the query vector and, for the bus, blew past the embedding token limit
(the only real 400 source in prod). Anchors are produced per-trigger and carried
via `trigger_extra_data["retrieval_anchor"]` → `ctx.trigger_extra_data` →
`step_1_select_narrative`. See the 2026-06-01 embedding-anchor design doc.

## 2026-05-20 — continuity keys off the last *visible* message (query OR response)

`select()`'s continuity gate changed from `if session.last_query:` to
`if session.last_query or session.last_response:`. Reason: when the agent
messages the user proactively (e.g. from a scheduled job), [[step_4_persist_results.py]]
anchors `last_response` + `current_narrative_id` but `last_query` is empty
(no preceding user query). The old gate skipped continuity entirely in that
case, so the user's short reply ("好") fell through to vector retrieval and
mis-routed. Now continuity runs whenever there is any prior visible exchange,
and [[continuity.py]] frames the proactive case as "the user is replying to a
message the agent sent". `is_continuous=True` still reuses
`session.current_narrative_id` (unchanged) — that anchor is now the chat-box
anchor, so it's the narrative the user is actually looking at.

## 2026-05-19 — select() 新增 `is_user_chat` 参数

修复 short-reply 后连续性判断崩的 bug。Session 的 `last_query` /
`last_response` / `current_narrative_id` 三个字段必须只反映**真实用户**的对话
轨迹，不能被 cron job / message_bus / lark / callback 这类 background trigger
覆盖。否则用户隔几分钟回复一个 "要" 时，`ContinuityDetector` 拿到的
`previous_query` 是 background trigger 的输入（cron payload / bus 消息），
连续性必然 False → 掉到 Top-K embedding 匹配 → 短消息 embedding 信息量极低 →
匹配错 Narrative 甚至新建。

- 新参数 `is_user_chat: bool = True`，由调用方根据 `ContextData.working_source`
  传入（`working_source == "chat"` → True，其它 → False）。
- `is_user_chat=False` 时整个 Session 更新分支被跳过，连续性判断仍然走，
  但 Phase 2 检索后**不**把 background trigger 的 query 写回 Session。
- 配套：[[step_4_persist_results.py]] 4.5 也加了同样的 source 判断，
  确保 `last_response` 同样只在 chat run 时被覆盖。

# narrative_service.py — Narrative 统一门面

## 为什么存在

AgentRuntime 在编排流水线时不应该知道"向量检索是怎么做的"或"embedding 是什么时候更新的"。`NarrativeService` 就是这层隔离：它把七八个私有实现类（`NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`、`PromptBuilder`、`ContinuityDetector`）统一包装成四类公开操作——select、update、CRUD、instance management。

## 上下游关系

**被谁用**：`agent_runtime/_agent_runtime_steps/step_1_select_narrative.py` 调 `select()`；`step_5_update_narrative.py` 调 `update_with_event()`；`services/module_poller.py` 的 `InstanceHandler` 通过 narrative 包直接访问（不经过 Service 层）；`backend/routes/` 偶尔调 CRUD 接口给前端查询。

**依赖谁**：构造时立即实例化 `NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`；`set_event_service()` 注入 `EventService`（懒注入，`EventService` 构造时不需要）；`_get_continuity_detector()` 懒加载 `ContinuityDetector`（避免在不需要的路径下支付 OpenAI SDK 初始化成本）。

## 设计决策

`select()` 的逻辑分两条路：如果 `ContinuityDetector` 判断当前 query 属于 session 里记录的那条 Narrative（连续性为真），就把那条 Narrative 置于第一位，再用 embedding 补充 Top-K 候选；如果连续性为假或没有 session，则走 `NarrativeRetrieval.retrieve_top_k()`（内部可以走 EverMemOS 或纯向量检索）。主 Narrative 强制排在第一位这个设计是有意的，确保 AgentRuntime 的 step_2 在 contextruntime 组装时总能优先渲染主线 events。

`update_with_event()` 有两个重要 flag：`is_main_narrative` 控制是否做完整的 LLM 动态更新（更新 name、current_summary、topic_keywords），`is_default_narrative` 控制是否只加 event_id 而跳过一切其他更新（default Narrative 是全局共享的兜底分类，不允许被某一次对话"污染"摘要）。

曾经考虑过把 `EventService` 在 `__init__` 时必须传入，但这会导致两个 Service 的构造产生顺序依赖，最终选择了 `set_event_service()` 的依赖注入模式。

## Gotcha / 边界情况

`_updater.set_vector_store(self._retrieval.vector_store)` 这行是为了让 `_retrieval` 和 `_updater` 共享同一个 `VectorStore` 实例——如果它们各自持有独立实例，embedding 更新后检索侧看到的还是旧值。这不是明显的 bug，是隐式状态共享，改代码时别把这行删掉。

连续性检测失败（LLM 报错）会静默 fallback 到"不连续"，不会抛出异常。这意味着偶发的 LLM 调用超时不会影响主流程，但会导致该轮对话建出一个新 Narrative，引起记忆碎片化。高并发下值得监控 `"Continuity detection failed"` 日志。

## 新人易踩的坑

`select()` 返回 `NarrativeSelectionResult`，不是 `List[Narrative]`——新代码如果直接当列表用会报属性错误。正确用法是 `result.narratives[0]` 取主 Narrative。

`session` 参数是**可变引用**：`select()` 内部会直接修改 `session.current_narrative_id`、`session.last_query` 等字段，调用方必须在 `select()` 之后再调用 `session_service.save_session(session)` 来持久化，否则下一次请求看到的 session 还是旧状态。
