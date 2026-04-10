---
code_file: src/xyz_agent_context/module/awareness_module/awareness_module.py
last_verified: 2026-04-10
---

# awareness_module.py — AwarenessModule 实现

## 为什么存在

AwarenessModule 是让 Agent 拥有"长期记忆用户偏好"能力的组件。它在每轮对话的数据收集阶段把 Awareness Profile 加载到 `ctx_data.awareness`，这个字段被 `prompts.py` 的 `{awareness}` 占位符填入系统提示，让 Agent 在整个对话中都知道"这个用户喜欢什么风格、有什么约定"。

**Hook 实现**：实现了 `hook_data_gathering`（从 `instance_awareness` 表加载 profile），未实现 `hook_after_event_execution`（用户偏好更新通过 MCP 工具而非 hook 完成）。

**MCP 端口**：7801

**Instance 模型**：Agent 级别（`is_public=1`），每个 Agent 只有一个实例，通过 `InstanceFactory.ensure_agent_instances_exist()` 在 Agent 创建时自动初始化。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载（capability module）；`HookManager` 调用 `hook_data_gathering`；`ModuleRunner` 启动 MCP 服务器
- **依赖谁**：`InstanceAwarenessRepository`（读写 profile 文本）；`InstanceRepository`（通过 agent_id 查找 instance_id）；`AgentRepository`（更新 agent_name）

## 设计决策

**`_get_instance_id()` 的双路径查找**：优先用 `self.instance_id`（由 `ModuleLoader` 注入），如果为 `None` 就通过 `agent_id + "AwarenessModule"` 查询数据库。这个 fallback 保证了 bootstrap 或数据库不完整时模块仍能工作，代价是一次额外的数据库查询。

**MCP 工具里用 `AwarenessModule.get_mcp_db_client()`**：MCP 工具在独立进程/线程里运行，不能使用 `self.db`。`get_mcp_db_client()` 是类方法，在当前进程里懒创建专属连接。

**首次使用自动创建默认 profile**：如果 `instance_awareness` 表里没有记录，`hook_data_gathering` 会自动写入一个默认的 "helpful assistant" profile，而不是让 `ctx_data.awareness` 为空。这防止了 LLM 因空 awareness 报错或行为异常。

## Gotcha / 边界情况

- **`instance_id` 为 `None` 时的行为**：如果 `_get_instance_id()` 返回 `None`（数据库里找不到实例记录），模块会用硬编码的默认 awareness 字符串继续运行，并打 warning 日志。这种情况通常说明 Agent 的实例记录没有正确初始化。
- **`init_database_tables()` 里的 SQL 是 MySQL 语法**：`DATETIME(6)` 和 `ON UPDATE CURRENT_TIMESTAMP(6)` 是 MySQL 专有语法，SQLite 不支持。实际表创建通过 `utils/database_table_management/create_instance_awareness_table.py` 进行，这个方法在生产中很少被直接调用。

## 新人易踩的坑

- 以为修改 Awareness Profile 可以通过直接写 `ctx_data.awareness` 来持久化——实际上 `ctx_data.awareness` 是每轮重新从数据库加载的，持久化必须通过 MCP 工具 `update_awareness` 调用 `InstanceAwarenessRepository.upsert()`。
- 在 `hook_data_gathering` 里调试时看到 awareness 是旧值——因为 MCP 工具在独立进程里更新了数据库，但当前进程的连接缓存可能还持有旧连接状态，通常重启即可。
