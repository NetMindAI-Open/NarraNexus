---
code_dir: src/xyz_agent_context/module/job_module/
last_verified: 2026-04-10
---

# job_module/ — 后台任务管理模块

## 目录角色

JobModule 让 Agent 具备"计划未来的能力"——创建在特定时间或条件下自动执行的后台任务（Job）。它是系统里唯一的 **task module**（`module_type="task"`），由 LLM 实例决策来决定是否创建、每个 Job 对应一个 ModuleInstance。

JobModule 的执行链跨越两个进程：
- **主进程**：Module hook 和 MCP 工具负责创建 Job、加载 Job 上下文、执行后分析结果
- **JobTrigger 进程**：独立后台进程，轮询 DB 找到到期 Job 并调用 AgentRuntime 执行

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `job_module.py` | Module 主体：hook_data_gathering 加载 Job 列表；hook_after_event_execution 分析执行结果；委托 MCP 给 `_job_mcp_tools.py` |
| `job_trigger.py` | 后台轮询服务：Worker Pool 模式，5 个并发 Worker；原子锁防重复执行；调用 AgentRuntime |
| `_job_mcp_tools.py` | MCP 工具：job_create、job_retrieval_*、job_update、job_pause、job_cancel |
| `_job_lifecycle.py` | LLM 分析：handle_job_execution_result（JOB 触发的执行结果分析）；update_ongoing_jobs_from_chat（CHAT 触发的 ONGOING 任务进度更新） |
| `_job_context_builder.py` | 执行上下文组装：依赖输出、社交网络信息、Narrative 摘要 → 执行提示词 |
| `_job_scheduling.py` | 下次执行时间计算：ONE_OFF、SCHEDULED（cron/interval）、ONGOING |
| `job_service.py` | 统一 Job 创建服务：ModuleInstance + Job 记录同时创建，处理依赖关系和向量生成 |
| `prompts.py` | Job 执行提示词模板 + ONGOING Chat 分析提示词 |

## 和外部目录的协作

- `repository/JobRepository`：唯一的 Job DB 操作通道，包含原子锁（`try_acquire_job`）和 ONGOING 状态追踪
- `services/ModulePoller`：监听 `module_instances` 表的状态变化，当 `JobTrigger` 把实例标记为 `completed` 时触发依赖链激活
- `SocialNetworkModule`：通过 `ctx_data.extra_data["related_job_ids"]` 把当前用户的关联 Job 传递给 JobModule
- `agent_framework/llm_api/embedding`：`job_create` 时生成向量用于语义检索（`job_retrieval_semantic`）
