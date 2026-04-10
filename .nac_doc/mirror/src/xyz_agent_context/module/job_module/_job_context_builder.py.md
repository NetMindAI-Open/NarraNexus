---
code_file: src/xyz_agent_context/module/job_module/_job_context_builder.py
last_verified: 2026-04-10
---

# _job_context_builder.py — Job 执行提示词的上下文组装器

## 为什么存在

从 `job_trigger.py` 分离出来（2026-03-06），把"给 AgentRuntime 准备 Job 执行提示词"这件事独立维护。JobTrigger 只负责调度，它不应该知道"这个 Job 的目标用户叫什么、关联的 Narrative 进展如何、依赖 Job 的输出是什么"——这些上下文组装逻辑集中在这个文件里。

核心入口是 `build_execution_prompt(db, job, user_timezone)`，把多段上下文（任务信息、关联实体、Narrative 摘要、依赖产出）拼接成一个完整的执行提示词，传给 `AgentRuntime.run()`。

## 上下游关系

- **被谁用**：`job_trigger._execute_job()` 调用 `build_execution_prompt()` 组装提示词，再传给 `AgentRuntime`
- **依赖谁**：`SocialNetworkRepository`（加载关联实体详情）；`InstanceRepository`（按 `agent_id + module_class` 查找 SocialNetworkModule 实例）；`NarrativeRepository`（加载 Narrative 的 `current_summary`）；`prompts.py` 里的四个模板常量（`JOB_TASK_INFO_TEMPLATE` 等）；`utils.timezone.format_for_llm()`（时区格式化）

## 设计决策

**三段可选上下文**：关联实体（`entities_section`）、Narrative 摘要（`narrative_section`）、依赖产出（`dependency_section`）都是条件性的——只要对应字段为空就输出空字符串，不影响模板渲染。这样 ONE_OFF Job 不强依赖任何关联数据就能执行。

**依赖产出通过 event 记录取**：`get_dependency_outputs()` 先从 `module_instances.dependencies` 取依赖实例列表，再从 `instance_jobs.process` 取历史 event_id，最后从 `events.final_output` 取最新一次执行输出。这条查询链跨越三张表，中间任一步失败都有降级（跳过该依赖项，继续处理其他依赖）。

**描述和 persona 截断**：`entity_description` 截取前 500 字符，`persona` 截取前 300 字符。这是为了控制提示词总长度。截断是硬截断，不保留完整句子边界——如果发现输出被截断到奇怪的位置，就在这里调整截断长度。

**`extra_requirement` 动态追加**：只有当 `dep_outputs`、`entities_info`、`narrative_summary` 中至少一个有内容时，才在提示词末尾加上"充分利用前置任务结果和上下文信息"的第 6 条要求。空 Job 不会看到多余的指示。

## Gotcha / 边界情况

**跨模块查询**：`load_social_network_context()` 内部动态导入 `SocialNetworkRepository` 和 `InstanceRepository`，并通过 `get_by_agent(agent_id, module_class="SocialNetworkModule")` 查找实例。如果 SocialNetworkModule 没有为这个 Agent 初始化过实例，返回空列表并返回空实体列表——Job 执行不会失败，但会缺少实体上下文。

**Narrative 摘要截至 800 字符**：`current_summary` 字段可能很长（Narrative 积累了大量对话后），`load_narrative_summary()` 强截前 800 字符。如果 Job 依赖后段的摘要内容，会被静默截掉。

## 新人易踩的坑

- `dependency_section` 里取的是依赖 Job 最后一次 event 的 `final_output`，不是 `_job_lifecycle.py` 分析生成的 `output` 字段——前者是 Agent 的原始输出，后者是 LLM 分析后的摘要。调试时注意区分这两个字段的语义。
- 这个文件对数据库有直接的 raw SQL 查询（`db.execute(query, params, fetch=True)`），不走 Repository 模式。如果未来切换数据库方言，这里的 `%s` 占位符需要调整。
