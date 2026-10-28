---
code_file: src/narranexus/platform/agent_runtime/_agent_runtime_steps/step_1_select_narrative.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-21 — 把路由的"无持久话题"判决抄上 ctx(冻结契约的置位端)

`ctx.no_durable_topic = bool(selection_result and
selection_result.no_durable_topic)` —— 契约的消费端与完整语义见
[[context.py]] 2026-08-21 条目与 [[step_4_persist_results.py]]。这里只记
置位端的一条纪律:**必须无条件赋值(含 False)**,不能只在为真时才写 ——
ctx 可能被复用,漏写会让上一轮的冻结判决泄漏到下一轮。

## 2026-08-20 — 开局把 bootstrap 问候语 seed 进 head narrative 实例（只一次）

选完 narrative、`_ensure_user_chat_instance` 建好每个 narrative 的 chat 实例后，对
**`narrative_list[0]`（head）** 的实例调用一次 seed：经 [[greeting_seed]] 判定问候文本，再交
[[_chat_writes]] 幂等落库，时间戳锚在 `ctx.event.created_at`（turn 起点）。

**为什么只 seed head、且放在循环外**：问候语的作用域是 (agent, user) 一次，不是 per-narrative。
首轮（非连续轮）`select()` 走 BM25 取 top-k（`MAX_NARRATIVES_IN_CONTEXT`，常规 2–3），新 agent 的
每个 narrative 都没有 chat 实例 → 循环里一次性建 2–3 个。若把 seed 放进 `_ensure_user_chat_instance`
就会写 2–3 条一样的问候语（首屏重复）。head 是 authored/primary thread，也正是
`ChatModule.persist_turn` 落库用的那个实例，其余是读侧 BM25 邻居。

`_ensure_user_chat_instance` 因此**不再** seed（回退了 per-narrative 的写法）。fast-select
(`step_1_fast_select`) 和 `step_4_persist_results` 这两个 `_ensure_user_chat_instance` 调用点也不
seed —— 它们靠 hook prepend 兜底（可接受，hook 仍会在首轮补上）。全程 best-effort。

## 2026-08-07 — 向 select() 传 trigger（E1 审计维度）

新增 `_trigger_label(ctx)`，把 `ctx.working_source` 转成字符串传给
`narrative_service.select(trigger=...)`。

为什么要单独记这一维：dev 实测（2026-08-07）chat 占 69% 的轮次、message_bus 占 30%，
而只有面向人的来源会移动 session 锚点（见本文件的 `_is_user_chat`）。两种来源走的是
不同的锚点语义，混在一起统计会把两种行为平均成一个无意义的比率。


## 2026-05-19 — 新 `_is_user_chat(ctx)` helper

Step 1 现在读 `ctx.working_source` 判断这一轮是不是回复真人 —— 通过
`[[hook_schema.py]] WorkingSource.is_from_human()`（CHAT / LARK / SLACK /
TELEGRAM → True；JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY → False）。
结果作为 `is_user_chat` 透传给 [[narrative_service.py]] `.select()`，确保
Session.last_query / current_narrative_id 只在人-回复轮被覆盖。两个
`narrative_service.select()` 调用点（forced fallback + normal）都已带上
这个参数。

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

## 2026-09-04 · ingress triggers (batch 3c.3)

The bootstrap greeting seed no longer imports the chat module: after `resolve_bootstrap_greeting_to_seed` the step fires `onDidResolveBootstrapGreeting` on `ctx.registries` (falling back to the process `KERNEL_REGISTRIES`); builtin.chat's `plugin_hooks` does the idempotent write. Head-only semantics, best-effort try/except and the hook-prepend fallback are unchanged.

## 2026-09-04 · chat instance by role (batch 5b.2)

The per-user chat instance is created for the module declaring role "chat" with its declared prefix.
