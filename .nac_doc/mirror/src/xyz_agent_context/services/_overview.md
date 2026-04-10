---
code_dir: src/xyz_agent_context/services/
last_verified: 2026-04-10
stub: false
---

# services/ — 后台长驻服务层

## 目录角色

`services/` 里的文件都是**独立进程级别的后台服务**或**一次性管理工具**，它们不属于 AgentRuntime 的同步执行流，而是在旁路异步地维护系统状态。

- `ModulePoller`：实时监听 Instance 状态变化，触发依赖链
- `MessageBusTrigger`（在 `message_bus/message_bus_trigger.py` 里）：监听 Agent 间消息
- `InstanceSyncService`：在 AgentRuntime 里同步调用，处理 LLM 输出的 Instance 决策
- `EmbeddingMigrationService`：切换 embedding 模型时的迁移工具
- `message_bus_poller.py`：轻量级函数封装，供集成测试或手动触发用

这层服务存在的根本原因是：某些操作（Job 完成后通知依赖项、Agent 间消息投递）在时间上是解耦的，不能阻塞用户的请求响应路径，必须在后台处理。

## 关键文件索引

| 文件 | 类型 | 说明 |
|------|------|------|
| `module_poller.py` | 独立进程 | 5秒轮询，Worker Pool 架构，处理 Instance 完成回调 |
| `instance_sync_service.py` | AgentRuntime 内调用 | 把 LLM 输出的 task_key 转为真实 instance_id，创建 Job 记录 |
| `embedding_migration_service.py` | 手动触发工具 | 切换 embedding 模型后重建所有向量 |
| `message_bus_poller.py` | 辅助函数 | 轻量封装 MessageBusService.get_pending_messages，用于测试或手动集成 |

## 和外部目录的协作

`module_poller.py` 是独立进程，通过 `make dev-poller` 启动（或 `uv run python -m xyz_agent_context.services.module_poller`）。它直接查询 `module_instances` 表，调用 `narrative.InstanceHandler`，再由 JobTrigger 负责执行已激活的 Job。

`instance_sync_service.py` 虽然在 `services/` 目录，但它**不是**独立后台进程，而是被 `agent_runtime/_agent_runtime_steps/step_3_decide_modules.py` 在执行流里同步调用的。它的特殊性在于需要访问多个 Repository（Instance、Job、SocialNetwork），集中管理比放在 AgentRuntime 步骤里更清晰。

`embedding_migration_service.py` 通过 `backend/routes/` 的 API 端点触发，不作为后台常驻进程运行。
