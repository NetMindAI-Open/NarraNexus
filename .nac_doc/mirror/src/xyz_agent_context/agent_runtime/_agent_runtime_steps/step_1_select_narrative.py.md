---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_1_select_narrative.py
last_verified: 2026-04-10
stub: false
---
# step_1_select_narrative.py — 流水线第 1 步：选择 Narrative

## 为什么存在

每次对话都需要找到对应的"记忆上下文"（Narrative）——是继续上一个话题还是开启新话题？是复用已有 Narrative 还是创建新的？这个决策依赖 LLM 推理（话题连续性检测）和向量检索（语义相似度匹配），两者都是耗时操作，需要支持取消信号中断。选出 Narrative 后还要确保当前用户在该 Narrative 中有独立的 ChatModule instance（多用户场景）。

## 上下游关系

输入：`ctx.session`（上次查询信息）、`ctx.awareness`（agent 上下文）、`ctx.input_content`（用户输入）、`ctx.forced_narrative_id`（Job trigger 指定的 Narrative）。

输出到 RunContext：`ctx.narrative_list`（选出的 Narrative 列表）、`ctx.query_embedding`（本次查询的向量，Step 3.2 的 ContextRuntime 会复用它）、`ctx.user_chat_instances`（每个 Narrative 对应的用户 ChatModule instance ID）、`ctx.evermemos_memories`（Phase 2 EverMemOS 缓存）。

关键依赖：`NarrativeService.select()` 封装了话题连续性检测 + 向量检索的完整逻辑。这个调用被 `_run_with_cancellation()` 包装，支持中途取消。

## 设计决策

**`_run_with_cancellation()` 而非普通 await**：`NarrativeService.select()` 内部有 LLM 调用，可能耗时 2-5 秒。`asyncio.wait()` 同时等待 select 任务和 cancellation event，哪个先完成就继续哪个；取消触发时立即 cancel 运行中的 select task，而不是等它完成再检查取消标志。这让用户"停止"响应在 Narrative 选择阶段也能快速生效。

**`forced_narrative_id` 的 fallback**：Job trigger 指定了 Narrative ID，但如果该 Narrative 被删除或不存在，自动 fallback 到正常选择流程，而不是报错。这保证了即使 Narrative 数据不完整，Job 执行也能继续（可能落到一个不同的 Narrative）。

**`_ensure_user_chat_instance()`**：每个用户在每个 Narrative 里都有自己独立的 ChatModule instance，记录该用户的聊天历史（而不是所有用户共用一个）。如果不存在就自动创建并关联到 Narrative。这支持了"销售 agent"等一个 agent 与多个用户对话的场景。

## Gotcha / 边界情况

- 选出的 `narrative_list` 可能包含多个 Narrative（主 Narrative + 相关 Narrative），`ctx.main_narrative` 是第一个（最相关的）。Step 4 会对所有 Narrative 都追加 Event，但只对第一个做完整的 LLM summary 更新。
- `retrieval_method` 字段记录了本次选择用了哪种方式（`evermemos`/`vector`/`fallback_vector`/`forced`），传给 ProgressMessage 的 details，便于调试 Narrative 选择行为。

## 新人易踩的坑

- `_ensure_user_chat_instance()` 内部使用 `get_db_client()` 获取数据库连接（而不是通过参数传入），这意味着它独立于 step 函数的 `db_client` 参数。如果两者是不同的连接对象（理论上 factory 返回同一单例，实际上没问题），但在测试时要注意 mock 的一致性。
- Session 在 Step 1 结束时才调用 `session_service.save_session()`，而不是在 Step 0 创建时。这是因为 Narrative 选择可能更新 Session 的 `current_narrative_id` 字段，需要选完后才持久化。
