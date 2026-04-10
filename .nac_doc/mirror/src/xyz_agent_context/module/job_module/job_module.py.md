---
code_file: src/xyz_agent_context/module/job_module/job_module.py
last_verified: 2026-04-10
---

# job_module.py — JobModule 实现

## 为什么存在

JobModule 是 AgentRuntime 侧的 Job 管理入口。它做三件事：在数据收集阶段把当前活跃 Job 的摘要注入系统提示（让 Agent 知道有哪些进行中的任务）；在执行后分析 Job 结果并更新状态；通过 MCP 工具暴露 Job CRUD 能力。

**Hook 实现**：实现了 `hook_data_gathering`（加载 Job 列表）和 `hook_after_event_execution`（分析执行结果，CHAT 路径还更新 ONGOING Job 进度）。

**MCP 端口**：7803

**Instance 模型**：task module，每个 Job 任务对应一个 ModuleInstance，LLM 在实例决策时创建或复用。

## 上下游关系

- **被谁用**：`ModuleLoader` 通过实例决策加载（task module）；`HookManager` 调用两个 hook；同时被 `_job_lifecycle.py` 的函数调用（注入 `repo` 和 getter）
- **依赖谁**：`JobRepository`（DB 操作）；`_job_mcp_tools.create_job_mcp_server`（MCP 创建）；`_job_lifecycle.handle_job_execution_result` 和 `update_ongoing_jobs_from_chat`（hook 后处理委托）

## 设计决策

**用户过滤逻辑**：`hook_data_gathering` 里的 `_collect_jobs` 根据 `current_user_id` 过滤，只展示与当前用户相关的 Job（`related_entity_id == user_id` 或 `user_id == creator` 或无 related_entity_id）。这防止了销售经理看到自己针对其他客户的 Job 时，那些 Job 出现在客户的对话上下文里。

**虚拟 JobModule 实例保证 MCP 工具可访问**：如果 LLM 决策没有选择任何 JobModule 实例，`ModuleLoader._ensure_job_module_available()` 会插入一个空 `instance_id` 的虚拟实例，保证 `job_create` 工具始终可用（否则 Agent 想创建 Job 但找不到工具）。虚拟实例的 `instance_id` 是空字符串，`hook_after_event_execution` 里会忽略它。

**hook_after_event_execution 的双路径**：JOB 触发 → `handle_job_execution_result` LLM 分析；CHAT 触发且有活跃 Job 实例 → `update_ongoing_jobs_from_chat` 检查 ONGOING 任务进度。两条路径互不干扰。

**`jobs_information` 占位符**：系统提示模板里有 `{jobs_information}` 占位符，由 `hook_data_gathering` 填充后通过 `get_instructions()` 格式化进入系统提示。这是 JobModule 与 prompt 集成的唯一通道。

## Gotcha / 边界情况

- **Job 状态更新的双入口竞争**：ONGOING Job 的状态由 `hook_after_event_execution`（入口 1，LLM 分析）和 `job_trigger._finalize_job_execution`（入口 2，机械更新）两处更新。入口 2 有"状态仍为 RUNNING 时才机械更新"的保护，但如果入口 1 的 LLM 调用比入口 2 慢，可能出现竞争窗口，详见 `job_trigger.py` 的注释。
- **`instance_ids` 里以 `job_` 前缀判断活跃 Job 实例**：`hook_after_event_execution` 通过 `[inst for inst in instance_ids if inst.startswith("job_")]` 收集活跃 Job。虚拟实例（空字符串）被跳过。

## 新人易踩的坑

- `instance_id`（Module 实例 ID，`job_xxxxxxxx`）和 `job_id`（Job 记录 ID，`job_xxxxxxxx` 但是不同的8位随机数）是两个不同的 ID，通过 `instance_jobs` 表的 `instance_id` 字段关联。混淆这两个 ID 会导致查询结果为空。
