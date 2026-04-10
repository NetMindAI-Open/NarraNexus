---
code_file: src/xyz_agent_context/module/job_module/job_service.py
last_verified: 2026-04-10
---

# job_service.py — Job 统一创建服务

## 为什么存在

Job 的创建需要同时写两张表（`module_instances` + `jobs`），还要处理依赖关系、生成向量嵌入、把 `related_entity_id` 同步到 SocialNetwork 的双向索引、把目标用户加入 Narrative 的 actors 列表。把这些步骤散在 MCP 工具或实例决策代码里会导致重复和不一致。`JobInstanceService` 作为统一创建入口，把这 11 步封装在一个事务性（尽管不是数据库事务）的方法 `create_job_with_instance()` 里。

## 上下游关系

- **被谁用**：`_job_mcp_tools.job_create`（对话中 Agent 调用）；`_module_impl/instance_decision.py` 的 `step_2_5` 批量创建路径
- **依赖谁**：`JobRepository`（写 jobs 表）；`InstanceRepository`（写 module_instances 表）；`NarrativeRepository`（权限校验、添加 PARTICIPANT actor）；`SocialNetworkRepository`（同步 `related_job_ids` 字段）；`get_embedding()` + `store_embedding()`（生成并双写向量）；`calculate_next_run_time()`（计算首次执行时间）

## 设计决策

**两层重复检测**：`create_job_with_instance()` 在真正创建前做了两次检查——先精确匹配同 `agent_id + user_id + title` 的活跃 Job，再用 Jaccard 相似度（阈值 0.5）模糊匹配标题相近的活跃 Job。两次检查都命中时返回 `is_existing=True`，不创建新 Job。这是为了应对 LLM 在多步骤请求里倾向于重复创建 Job 的问题。代价是：正常情况下两个标题相近但实际不同的 Job 会被错误合并，相似度阈值 0.5 偏保守。

**依赖关系与 BLOCKED 状态**：如果传入了 `dependencies`（非空列表），`ModuleInstance` 的初始状态直接设为 `BLOCKED`，由 `ModulePoller` 监听依赖项完成后激活。不会校验依赖项的 instance_id 是否真实存在——传入不存在的 ID 会导致 Job 永久卡在 BLOCKED。

**`create_jobs_batch()` 的拓扑排序是伪实现**：批量创建 Jobs 时应该按依赖顺序（先建被依赖的 Job）。目前代码里有 Kahn 算法的框架，但核心循环里 `pass` 了，实际是按 `depends_on` 数量排序。这在简单的链式依赖里能工作，但有循环依赖时会静默失败。

**`related_entity_id` → SocialNetwork 双向同步**：创建 Job 时如果指定了 `related_entity_id`，会调用 `SocialNetworkRepository.append_related_job_ids()` 把 job_id 写到 Entity 的 `related_job_ids` 字段。这个同步是"尽力而为"——失败只记录 error 日志，不中断 Job 创建。如果同步失败，`hook_data_gathering` 里 SocialNetworkModule 就无法把 `related_job_ids` 写入 `ctx_data.extra_data`，JobModule 就拿不到与当前用户关联的 Job 列表。

**`update_job()` 里的 Type A/B/C 操作**：`append_to_payload` 是 Type A（补充指示），直接 concat 到现有 payload；修改 `next_run_time` 是 Type B（立即执行）；修改 `status` 是 Type C（暂停/取消）。Type A 会在 payload 末尾追加一个带 `## Manager Supplementary Guidance` 标题的分节——这是系统约定的格式，`_job_context_builder.py` 不做特殊处理，原样传给 Agent 执行。

## Gotcha / 边界情况

- **Narrative 权限校验**：如果 `narrative_id` 非空，会校验 `user_id` 是否是 Narrative 的 Creator（`NarrativeActorType.USER` 类型的 actor）。不是 Creator 会返回 `success=False`，不会创建 Job。这个校验在多用户场景下很重要，但可能意外拒绝授权用户（如果 Narrative 的 actors 配置有误）。

## 新人易踩的坑

- 向量生成失败不会中断 Job 创建（`get_embedding()` 失败会让 `embedding=None`），但语义检索（`job_retrieval_semantic`）将对这个 Job 永久失效。
- `_diff_sync_entity()` 在修改 `related_entity_id` 时做"删旧加新"的双向同步。如果 remove 旧关联失败但 add 新关联成功，会导致旧 Entity 和新 Entity 都关联了这个 Job，形成脏数据。
