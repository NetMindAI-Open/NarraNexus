---
code_dir: src/xyz_agent_context/agent_runtime/
last_verified: 2026-04-10
stub: false
---
# agent_runtime/ — Agent 执行流水线的编排层

## 目录角色

这个目录是架构中的"编排层"，负责协调一次完整 agent turn（用户输入 → agent 回复 + 持久化）的所有步骤。它不包含任何业务逻辑（那些在 `narrative/`、`module/`、`context_runtime/` 里），只负责按序驱动各步骤、传递状态、处理横切关注点（取消、日志、cost tracking）。

## 关键文件索引

- **`agent_runtime.py`**：编排器主体，`run()` 方法驱动 7 步流水线，处理 LLM 配置加载、后台 hooks 调度
- **`_agent_runtime_steps/context.py`**：`RunContext` dataclass，流水线所有步骤共享的黑板状态
- **`_agent_runtime_steps/`**：各步骤的具体实现，见下文详细索引
- **`cancellation.py`**：`CancellationToken` 协作式取消机制
- **`execution_state.py`**：Agent Loop 执行过程的不可变状态追踪器（token 计数、tool call 序列）
- **`logging_service.py`**：每次 run 独立日志文件的创建/清理
- **`response_processor.py`**：原始 Claude SDK 事件 → 类型化 schema 对象的无状态转换器

## `_agent_runtime_steps/` 各步骤职责

| 步骤文件 | 职责 | RunContext 写入字段 |
|---------|-----|-------------------|
| `step_0_initialize.py` | 加载 agent 配置、创建 Event、获取 Session、加载 awareness | `agent_data`, `event`, `session`, `awareness`, `module_service` |
| `step_1_select_narrative.py` | 选择/创建对应 Narrative、确保用户 ChatModule instance | `narrative_list`, `query_embedding`, `user_chat_instances` |
| `step_1_5_init_markdown.py` | 读取 Narrative 的 Markdown 历史、保存 previous_instances 快照 | `markdown_history`, `previous_instances` |
| `step_2_load_modules.py` | LLM 决策激活哪些 Module Instance，选择执行路径 | `load_result`, `module_list` |
| `step_2_5_sync_instances.py` | 同步 Instance 变更到数据库，创建 Job 记录 | `created_job_ids`；更新数据库 |
| `step_3_execute_path.py` | 路由到 AGENT_LOOP 或 DIRECT_TRIGGER | `execution_result` |
| `step_3_agent_loop.py` | ContextRuntime 构建 + ClaudeAgentSDK 执行 + 流式输出 | `execution_result`（via step_3_execute_path） |
| `step_3_direct_trigger.py` | 直接调用 MCP 工具（跳过 LLM） | 返回 `PathExecutionResult` |
| `step_4_persist_results.py` | 持久化 Event、Narrative、Trajectory、Session、cost | 数据库写入 |
| `step_5_execute_hooks.py` | 调用所有 Module 的 `hook_after_event_execution` | 触发后处理（内存写入、entity 提取等） |
| `step_display.py` | 纯工具函数：格式化各步骤的展示数据 | 无 |

## 和外部目录的协作

- `backend/routes/` 实例化 `AgentRuntime` 并调用 `run()`，把 `CancellationToken` 传进来
- `narrative/` 包提供 `EventService`、`NarrativeService`、`SessionService`，被各 step 函数直接调用
- `module/` 包提供 `ModuleService` 和 `HookManager`
- `context_runtime/` 在 Step 3.2 被调用，负责把 RunContext 中的状态构建成 LLM 消息列表
- `agent_framework/` 的 `ClaudeAgentSDK` 在 Step 3.4 被调用执行实际 LLM 推理
