---
doc_type: reference
last_verified: 2026-04-10
scope:
  - src/xyz_agent_context/
  - backend/
  - frontend/src/
  - tauri/src-tauri/src/
related_playbooks:
  - ../playbooks/add_new_module.md
  - ../playbooks/debug_runtime.md
---

# NexusAgent 架构参考

## 1. 架构分层总览

系统采用 7 层架构，依赖方向严格自上而下：上层可调用下层，下层不得反向引用上层。

```
API Layer (FastAPI Routes)        ← 控制层
AgentRuntime (Orchestrator)       ← 编排层（7步流水线）
Services (Narrative, Module)      ← 服务协议层
Implementation (_*_impl/)         ← 私有实现层
Background Services (services/)   ← 后台服务层
Repository (Data Access)          ← 数据访问层
AsyncDatabaseClient + Schema      ← 数据层
```

| 层级 | 目录 | 职责 |
|------|------|------|
| **控制层** | `backend/routes/` | HTTP/WebSocket 端点，负责请求接收、参数校验与响应序列化。不包含业务逻辑。 |
| **编排层** | `agent_runtime/` | 核心流水线调度器 `AgentRuntime`，按步骤串联各 Service 完成一次完整的 Agent 运行周期。 |
| **服务协议层** | `narrative/narrative_service.py`, `module/module_service.py` | 对外暴露统一接口，隐藏实现细节。采用 Bridge 模式委托给 `_*_impl/` 完成实际工作。 |
| **私有实现层** | `_narrative_impl/`, `_module_impl/`, `_agent_runtime_steps/` | 具体业务逻辑实现，以 `_` 前缀标识为私有包，不对外导出。 |
| **后台服务层** | `services/` | 长期运行的后台进程，如 `ModulePoller`（轮询 Instance 状态变更触发依赖链）、`InstanceSyncService`（同步 Instance 与 Narrative 关联）。 |
| **数据访问层** | `repository/` | 基于 `BaseRepository` 泛型基类的纯 CRUD 操作，解决 N+1 查询问题。每个业务实体对应一个 Repository 类。 |
| **数据层** | `schema/`, `utils/db/db_factory.py` | Pydantic 数据模型定义（`schema/`）与全局单例 `AsyncDatabaseClient`（`db_factory.py`）。 |

## 2. 7 步流水线

`AgentRuntime` 的核心是一条 7 步流水线，实现文件位于 `agent_runtime/_agent_runtime_steps/` 目录下。

| 步骤 | 说明 | 实现文件 |
|------|------|---------|
| **Step 0: Initialize** | 加载 Agent 配置与 Session 信息，初始化运行上下文 `RuntimeContext`。若 Agent 不存在或被禁用则提前终止。 | `step0_initialize.py` |
| **Step 1.5: Init Markdown** | 构建初始 Markdown 结构，用于后续上下文拼接。设置系统提示词模板和基础格式框架。 | `step1_5_init_markdown.py` |
| **Step 2: Load Modules** | 加载当前 Agent 激活的所有 Module Instance，调用各模块的 `hook_data_gathering` 收集上下文数据，并根据触发来源决定执行路径（`agent_loop` 或 `direct_trigger`）。 | `step2_load_modules.py` |
| **Step 2.5: Sync Instances** | 建立或移除 Instance 与 Narrative 的关联关系。确保新创建的 Instance 绑定到正确的 Narrative，已删除的 Instance 解除关联。 | `step2_5_sync_instances.py` |
| **Step 3: Execute Path** | 根据 Step 2 确定的执行路径运行核心逻辑。`agent_loop` 路径调用 LLM 进行多轮对话；`direct_trigger` 路径直接执行预定义动作而不经过 LLM。 | `step3_execute_path.py` |
| **Step 4: Persist Results** | 持久化运行结果：保存新产生的 Event、更新 Narrative 状态与摘要、记录 Token 消耗量等统计信息。 | `step4_persist_results.py` |
| **Step 5: Execute Hooks** | 遍历所有激活模块，依次调用 `hook_after_event_execution`。模块在此阶段执行后处理逻辑，如更新外部系统、触发下游任务等。 | `step5_execute_hooks.py` |

## 3. Trigger 架构

外部事件进入 `AgentRuntime` 有 4 种模式。新增模块若涉及外部集成，必须理解此架构。

### Pattern A: 请求驱动（Per-request）

- **WebSocket 路由** (`backend/routes/websocket.py`)：客户端通过 WebSocket 发送 JSON 消息，Runtime 流式返回响应。这是前端聊天的主要入口。
- **A2A HTTP 服务** (`chat_trigger.py`)：符合 JSON-RPC 2.0 协议，通过 `metadata` 中的 `agent_id` 路由到目标 Agent。

### Pattern B: 共享轮询 + 路由分发（Shared poller + routing）

- **MessageBusTrigger** (`message_bus_trigger.py`)：每 10 秒轮询 `bus_messages` 表，按 `channel_id` 分组，过滤 @mention 消息，路由到对应 Agent。3 个并发 Worker。
- **JobTrigger** (`job_trigger.py`)：轮询 `jobs` 表中到期任务，5 个并发 Worker。
- **ModulePoller** (`module_poller.py`)：轮询 Instance 状态变更，触发模块依赖链。

### Pattern C: 事件订阅（Event subscription）

尚未实现，但这是下一代外部 IM 集成（如飞书）的目标模式：一个共享订阅者接收事件流，经事件路由器分发到对应 Agent。

### 核心设计原则

**对于 N 个 Agent，绝不创建 N 个监听器/进程。** 始终使用共享轮询器/订阅者 + 路由逻辑。这是从 `MessageBusTrigger` 的实践中提炼的核心经验。

### AgentRuntime.run() 签名

```python
async def run(
    agent_id, user_id, input_content,
    working_source=WorkingSource.CHAT,
    trigger_extra_data=None,
    job_instance_id=None,
    forced_narrative_id=None,
    cancellation=None,
) -> AsyncGenerator[RuntimeMessage, None]
```

## 4. WorkingSource 枚举

`WorkingSource` 标识当前运行的触发来源，取值包括：

`CHAT` | `JOB` | `A2A` | `CALLBACK` | `SKILL_STUDY` | `MATRIX` | `MESSAGE_BUS`

新增 Trigger 时必须同步添加对应的 `WorkingSource` 值。下游依赖此枚举的关键位置：

- **Module Hooks**：模块根据 `working_source` 调整行为（如 `JOB` 来源跳过某些交互式逻辑）
- **Narrative 连续性检测**：不同来源使用不同的时间阈值判断是否延续同一 Narrative
- **上下文构建**：不同来源使用不同的 Prompt 模板（如 `MESSAGE_BUS` 来源注入频道上下文）

## 5. Channel 系统

Channel 系统负责多渠道消息的统一接入与回复分发。

### ChannelTag

定义于 `schema/channel_tag.py`，是附加到每条进入 `AgentRuntime` 消息上的统一来源标识。核心字段：

| 字段 | 说明 |
|------|------|
| `channel` | 渠道标识（如 `lark`, `slack`, `web`） |
| `sender_name` | 发送者显示名 |
| `sender_id` | 发送者唯一 ID |
| `room_id` | 群/房间 ID |
| `room_name` | 群/房间名称 |

### ChannelSenderRegistry

定义于 `channel/channel_sender_registry.py`，维护 `channel -> sender_function` 的注册表。当 Agent 生成响应后，系统根据消息来源的 `channel` 查找对应的 sender 函数，将回复投递回原渠道。**新增 IM 集成必须在此注册 sender。**

### ChannelContactUtils

定义于 `channel/channel_contact_utils.py`，提供跨渠道联系人解析能力，用于在不同渠道间关联同一用户身份。

## 6. 设计模式速查

| 模式 | 应用位置 | 作用 |
|------|---------|------|
| 依赖注入 | `AgentRuntime` | 构造函数接收 `LoggingService`、`ResponseProcessor`、`HookManager` 等服务实例，便于测试和替换。 |
| Repository 模式 | `repository/` | `BaseRepository` 泛型基类封装 CRUD，统一分页、批量查询，解决 N+1 问题。 |
| 服务协议层 + Bridge | `NarrativeService`, `ModuleService` | 对外暴露稳定接口，内部委托 `_*_impl/` 实现，隔离变更影响。 |
| 工厂/单例 | `db_factory.py` | 全局唯一 `AsyncDatabaseClient` 实例，统一连接池管理。 |
| Hook 模式 | `module/base.py` | 生命周期钩子 `hook_data_gathering` 和 `hook_after_event_execution`，模块通过钩子参与流水线而无需修改核心逻辑。 |
| 共享轮询 + 路由 | `MessageBusTrigger`, `JobTrigger` | 单进程轮询 + 路由分发，避免为每个 Agent 创建独立监听器，实现可扩展的事件处理。 |
