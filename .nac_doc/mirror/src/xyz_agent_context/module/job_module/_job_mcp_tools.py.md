---
code_file: src/xyz_agent_context/module/job_module/_job_mcp_tools.py
last_verified: 2026-04-10
---

# _job_mcp_tools.py — JobModule MCP 工具定义

## 为什么存在

从 `job_module.py` 分离出来（2026-03-06 重构），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期解耦。`job_module.py` 专注于数据收集和执行后处理，这个文件专注于 Agent 如何通过 MCP 工具管理 Job。

提供 7 个工具：`job_create`、`job_retrieval_semantic`、`job_retrieval_by_id`、`job_retrieval_by_keywords`、`job_update`、`job_pause`、`job_cancel`。

## 上下游关系

- **被谁用**：`JobModule.create_mcp_server()` 调用 `create_job_mcp_server(port, JobModule.get_mcp_db_client)`；`ModuleRunner` 部署返回的 FastMCP 实例；`JobModule.get_instance_object_candidates()` 通过 `fastmcp.Client` 内存调用 `job_retrieval_semantic`
- **依赖谁**：`JobRepository`（DB 操作）；`get_embedding()`（`job_create` 时生成语义向量）；`job_service.JobInstanceService`（创建 ModuleInstance + Job 的统一服务）

## `agent_id` 如何传入

所有工具都要求显式传入 `agent_id` 和 `user_id`。MCP 工具在独立进程里没有"当前 Agent 上下文"，必须由 LLM 从系统提示里读取并传入。`JobModule.__init__` 里的 instructions 包含 `Your agent_id is {agent_id}` 提示，让 LLM 知道该用哪个值。

## 设计决策

**`job_create` 的强防重创建约束**：工具 docstring 里有大量"CHECK IF I ALREADY CREATED JOBS FIRST"的警告，以及详细的何时/何时不用该工具说明。这是因为 LLM 在接收用户"多步骤"请求时容易重复创建 Job（见实例决策提示词里也有同样的 WARNING）。

**`depends_on_job_ids` vs `dependencies`**：工具参数用 `depends_on_job_ids`（实例 ID 列表），内部转为 `dependencies`（DB 字段）。这个命名隔离是为了让 LLM 传入的是 job 的 `instance_id`（如 `job_a1b2c3d4`），而不是 `job_id`（DB 主键）。两者都是 8 位随机后缀格式，容易混淆。

**语义检索的向量**：`job_create` 时调用 `get_embedding()` 生成向量存入 DB，`job_retrieval_semantic` 时对查询文本也生成向量做余弦相似度检索。向量生成失败时 `job_create` 不会中断（向量字段可以为空，但语义检索功能会失效）。

## Gotcha / 边界情况

- **`related_entity_id` 的语义**：如果 Job 是"Agent 为自己做的事并向请求者汇报"，`related_entity_id` 填请求者的 `user_id`；如果 Job 是"针对另一个用户的行动（如销售跟进）"，填目标用户的 `user_id`。这个区分决定了 JobTrigger 执行时加载哪个用户的上下文，非常关键但容易搞错。

## 新人易踩的坑

- `job_cancel` 会把 Job 标记为 `CANCELLED` 并同时把 `module_instances` 里的实例标记为 `completed`（触发 ModulePoller 的依赖链）。取消一个 Job 可能意外激活等待该 Job 的下游 Job。
