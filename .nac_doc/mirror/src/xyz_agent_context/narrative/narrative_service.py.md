---
code_file: src/xyz_agent_context/narrative/narrative_service.py
last_verified: 2026-04-10
stub: false
---

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
