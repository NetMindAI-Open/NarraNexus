---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_2_5_sync_instances.py
last_verified: 2026-04-10
stub: false
---
# step_2_5_sync_instances.py — 流水线第 2.5 步：Instance 变更同步到数据库

## 为什么存在

Step 2 的 Module 决策产出了"应该激活哪些 Instance"的决定，但这只是内存中的状态。数据库里的 `instance_narrative_links` 表是持久化的真相来源，也是 `ModulePoller` 用来发现需要触发的 Job Instance 的来源。这个步骤把内存决策同步到数据库：新 Instance 建立关联、已完成 Instance 移入历史、在途 Instance 保持活跃、Markdown 文件更新反映当前状态、新 JobModule Instance 创建对应的 Job 记录。

## 上下游关系

输入：`ctx.load_result`（Step 2 的模块决策结果）、`ctx.main_narrative`。

输出：数据库 `module_instances`、`instance_narrative_links`、`jobs` 表的写入；`ctx.created_job_ids`（Step 3.2 传给 ContextRuntime，把新建 Job 的信息加入 LLM 上下文）；`main_narrative.active_instances` 运行时缓存更新。

关键外部依赖：`InstanceRepository`、`InstanceNarrativeLinkRepository`（直接从 `db_factory` 获取连接，不通过函数参数传入）、`InstanceSyncService`（创建 Job 记录）。

## 设计决策

**已完成 Instance 才移入历史**：`removed_ids` 中的 Instance 只有当数据库中状态为 `completed`/`failed`/`cancelled` 时才调用 `unlink(to_history=True)`。还在进行中的 Instance（`active`/`running`/`blocked`/`pending`）即使 LLM 决策"不需要它了"，也保持活跃关联——因为 `ModulePoller` 还在监听它的完成事件，移除关联会让 Poller 找不到该 Instance。

**JobModule 孤儿实例保护**：JobModule Instance 必须有 `job_config` 才能创建 `ModuleInstanceRecord`。没有 `job_config` 的 JobModule（LLM 只说了"需要 Job 功能"但没给具体配置）会被跳过，不创建孤儿记录。同时从 `added_ids` 中移除，避免建立 Instance-Narrative 关联。

**`InstanceSyncService.create_jobs_for_instances()`** 负责把 `raw_instances` 中的 `job_config` 转化为 `jobs` 表记录，包括设置 `narrative_id`（Feature 3.1：把 Job 关联到触发它的 Narrative）。

## Gotcha / 边界情况

- `db_client` 在函数内部通过 `get_db_client()` 获取，而不是来自函数参数。这意味着如果有连接问题，错误在这里而不是调用方出现。
- 步骤顺序重要：先更新 Markdown（2.5.1），再同步数据库（2.5.2），再创建 Job（2.5.3）。如果 2.5.2 失败，Job 也不会创建，保持一致性。但没有事务保护整个步骤，部分失败会导致中间状态。

## 新人易踩的坑

- `load_result.key_to_id` 是任务键（LLM 决策时用的标识符）到实际 instance_id 的映射。`raw_instances` 里的 `task_key` 需要通过这个映射解析出 `resolved_id`，才能判断它是否在 `added_ids` 里。直接用 `raw_instances[i].instance_id` 可能是未解析的 key 而不是真实 ID。
