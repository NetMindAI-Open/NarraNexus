---
code_file: src/xyz_agent_context/services/instance_sync_service.py
last_verified: 2026-04-10
stub: false
---

# instance_sync_service.py — LLM Instance 决策结果的同步处理

## 为什么存在

AgentRuntime 的 step_3 让 LLM 决策"接下来需要哪些 Module Instance"，LLM 的输出是带语义 `task_key`（如 `"research_task_1"`）的列表。但数据库里存的是真实的 `instance_id`（如 `"job_fe7382f7"`）。`InstanceSyncService` 是这个转换桥梁：它把 LLM 输出的语义 task_key 映射成真实 ID，解析依赖关系（`depends_on` task_key 列表 → `dependencies` instance_id 列表），检测循环依赖，并为 JobModule 的 Instance 创建对应的 Job 数据库记录。

## 上下游关系

**被谁用**：`agent_runtime/_agent_runtime_steps/step_3_decide_modules.py` 在 LLM 返回 Instance 决策后立即调用 `process_instance_decision()` 和 `create_jobs_for_instances()`。这是同步调用，在请求处理流程里阻塞执行。

**调用谁**：`repository.JobRepository` 创建 Job 记录；`repository.InstanceRepository` 创建 SocialNetworkModule 实例（当 Job 需要绑定 Entity 时）；`repository.SocialNetworkRepository` 更新 Entity 的 `related_job_ids`；`repository.NarrativeRepository` 给 Narrative 添加 PARTICIPANT actor；`agent_framework/llm_api/embedding.py` 为每个 Job 生成 embedding。

## 设计决策

Job 去重逻辑有两层：**批次内去重**（同一批决策里标题相同的 Job 只创建一个）和**历史去重**（与已有 active Job 的标题做 n-gram 相似度对比，超过 0.5 则跳过）。这是为了防止 LLM 在同一个 Narrative 里重复创建语义相同的 Job（比如第一轮创建了"联系客户 A"，第二轮又创建一个"给客户 A 发邮件"）。去重算法是基于字符级 bigram + 子字符串包含检测的启发式方法，不是语义向量比较，因此可能有误判。

依赖关系通过 DFS 循环检测（`_detect_circular_dependencies()`），在 `process_instance_decision()` 里如果检测到循环会抛 `ValueError`，AgentRuntime 需要捕获并处理（目前是让整个 step_3 失败）。

非 JobModule 的 Instance（ChatModule、SocialNetworkModule 等）即使带了 `depends_on`，也会被强制清空依赖并设为 `active`——这些是能力型 Module，它们的激活不应该被阻塞。

## Gotcha / 边界情况

`create_jobs_for_instances()` 在创建每个 Job 时都会调用 `get_embedding()`，即每个 Job 一次 API 调用。如果 LLM 一次决策产生 10 个 JobModule Instance，这里会发出 10 次 embedding 请求。要注意 API rate limit。

`_sync_job_to_entity()` 在找不到 SocialNetworkModule 实例时会**自动创建**一个，找不到目标 Entity 时也会**自动创建**一个空壳 Entity。这个自动创建行为有时会产生意外的空 Entity 记录。`EmbeddingMigrationService` 的清理逻辑会删除没有名字也没有描述的空壳 Entity。

Job 记录通过 `instance_id` 字段做唯一约束检查（`get_jobs_by_instance(instance_id)`）——如果同一个 instance_id 对应的 Job 已存在，直接返回已有 job_id，不报错也不更新。所以"修改 Job 内容"不能通过重复调用 `create_jobs_for_instances` 实现，需要走独立的 update 接口。

## 新人易踩的坑

`MODULE_PREFIX_MAP` 是模块名到 instance_id 前缀的静态映射，新增 Module 时必须在这里登记，否则生成的 instance_id 会用默认前缀 `"inst"` 而不是模块专属前缀，影响可读性和 ID 格式一致性。

`task_key` 只是临时的语义标签，不存入数据库。`instance_id` 才是持久化的 ID。在 `process_instance_decision()` 的返回值里，`InstanceDict.instance_id` 已经被替换成真实 ID，`InstanceDict.task_key` 还保留着 LLM 原始输出，别搞混。
