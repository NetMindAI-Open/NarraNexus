---
code_file: src/xyz_agent_context/narrative/event_service.py
last_verified: 2026-04-10
stub: false
---

# event_service.py — Event 生命周期门面

## 为什么存在

Event 是系统里"一次完整 Agent 执行"的原子记录单位，包含触发源、完整 event_log、所有 Module Instance 快照和最终回复。`EventService` 是这套记录的统一入口，把 Event 的 CRUD（委托给 `_event_impl/crud.py`）与 Event 的处理逻辑（委托给 `_event_impl/processor.py`）暴露为简单的异步方法，让 AgentRuntime 不需要直接操作数据库或管理 embedding 生成时机。

## 上下游关系

**被谁用**：`agent_runtime/_agent_runtime_steps/step_2_prepare_context.py` 调 `select_events_for_context()` 筛选历史事件注入上下文；`step_4_execute.py` 调 `create_event()` 在执行前登记 Event；执行完成后调 `update_event_in_db()` 写入 final_output 和 event_log。`NarrativeService` 的内部实现也依赖 EventService 获取 Event embedding 做 Narrative 匹配增强。

**依赖谁**：构造时接受可选的 `event_repository`（`EventRepository`）和 `event_loader`（`DataLoader` 泛型，解决批量加载的 N+1 问题）。若不传，`EventCRUD` 会在首次调用时懒加载默认 DB 客户端。`EventProcessor` 的 `update_event()` 内部会调用 `get_embedding()` 生成向量，因此更新操作会触发 embedding API 调用。

## 设计决策

`select_events_for_context()` 使用混合策略：最近 N 条保证对话连贯性，相关性 Top-K 保证查询关联性，最后去重合并按时间排序。参数全部有默认值（从 `config.py` 读取），正常使用时无需传参。

曾考虑把 embedding 生成放在 `create_event()` 阶段，但 create 时 final_output 还没有，embedding 质量差。最终放在 `update_event_in_db()`（`generate_embedding=True` 默认开启），此时 input + output 都齐全。代价是 create 和 update 都必须被调用，中途崩溃会产生没有 embedding 的孤儿 Event——这些孤儿会被 `EmbeddingMigrationService` 在重建时补齐。

`duplicate_event_for_narrative()` 是给"同一次 Event 需要关联到多条 Narrative"的场景用的（比如 Job 完成通知需要同时更新主 Narrative 和全局日志 Narrative）。

## Gotcha / 边界情况

`update_event_in_db()` 的 `generate_embedding=True` 默认值意味着每次更新都会调用 embedding API。在测试中如果不想产生外部 API 调用，记得传 `generate_embedding=False`。

`EventService.events` 是一个实例级列表，在 `__init__` 初始化为空后从未被主流程读写——这是历史遗留字段，目前是死代码，不要依赖它。

## 新人易踩的坑

`load_events_from_db()` 返回 `List[Optional[Event]]`，对应位置若找不到则为 `None`，不会抛异常也不会缩短列表长度。按 index 一一对应时必须自行处理 None。

`get_event_head_tail_prompt()` 是静态方法，返回 dict 包含 `head` 和 `tail` 两个 key，用于在上下文里包裹 Event 列表。如果只调了 `combine_event_prompt` 但忘了加 head/tail，LLM 看到的上下文格式会不完整。
