---
code_dir: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/
last_verified: 2026-04-10
stub: false
---
# _agent_runtime_steps/ — 流水线各步骤实现

## 目录角色

这个目录包含 `AgentRuntime.run()` 7 步流水线中每个步骤的具体实现。步骤函数从 `__init__.py` 导出供 `agent_runtime.py` 使用。所有步骤函数共享同一个 `RunContext` 对象（定义在 `context.py`），通过读写 context 字段传递数据。

下划线前缀（`_agent_runtime_steps`）标志这是私有实现目录，外部代码只能通过 `agent_runtime.py` 间接使用，不应该直接 import 这里的步骤函数。

## 关键文件索引

- **`context.py`**：`RunContext` dataclass 定义，所有步骤共享的状态容器
- **`step_0_initialize.py`**：初始化阶段（agent 配置、Event、Session、Awareness）
- **`step_1_select_narrative.py`**：Narrative 选择，含取消信号包装和 ChatModule instance 确保
- **`step_1_5_init_markdown.py`**：Markdown 历史初始化（无 ProgressMessage，静默执行）
- **`step_2_load_modules.py`**：Module 决策，产出 `ModuleLoadResult`
- **`step_2_5_sync_instances.py`**：Instance-数据库同步 + Job 记录创建（包含原 step_2_6）
- **`step_3_execute_path.py`**：路由分发，根据 `execution_type` 分发到 agent_loop 或 direct_trigger
- **`step_3_agent_loop.py`**：主 agent loop（ContextRuntime + ClaudeAgentSDK），产出流式消息
- **`step_3_direct_trigger.py`**：直接 MCP 工具调用路径（绕过 LLM）
- **`step_4_persist_results.py`**：结果持久化（Trajectory、Markdown stats、Event、Narrative、cost）
- **`step_5_execute_hooks.py`**：Module hook 执行，产出 callback_results
- **`step_display.py`**：展示格式化工具函数（纯工具，无状态，被 `response_processor.py` 和各步骤调用）

## 和外部目录的协作

- `step_3_agent_loop.py` 跨出目录调用 `context_runtime.ContextRuntime` 和 `agent_framework.ClaudeAgentSDK`
- `step_2_5_sync_instances.py` 直接使用 `repository/` 层的 `InstanceRepository` 和 `InstanceNarrativeLinkRepository`，以及 `services.InstanceSyncService`
- `step_5_execute_hooks.py` 把 hook 参数构建后委托给 `module.HookManager` 执行
- `step_display.py` 被 `response_processor.py`（上级目录）import，是少数从内部到外部的依赖，是合理的工具函数提升
