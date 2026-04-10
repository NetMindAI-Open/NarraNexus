---
code_file: src/xyz_agent_context/module/chat_module/chat_trigger.py
last_verified: 2026-04-10
---

# chat_trigger.py — A2A 协议 API Server

## 为什么存在

`chat_trigger.py` 是整个 Agent 系统对外的标准接入层。它实现了 Google A2A（Agent-to-Agent）协议 v0.3，让外部 Agent 或客户端可以通过标准化的 JSON-RPC 2.0 接口与 AgentRuntime 交互，无需了解内部实现细节。

这个文件虽然在 `chat_module/` 下，但它实际上是整个 `AgentRuntime` 的外部 HTTP 网关，不仅仅是聊天功能的入口。`ModuleRunner.run_api_server()` 和 `run_module()` 都直接引用它。

## 上下游关系

- **被谁用**：`ModuleRunner.run_api_server()` 和 `run_module()` 在部署时启动 `A2AServer`；`run.sh` 通过 `make dev-backend` 间接触发
- **依赖谁**：`AgentRuntime`（`agent_runtime/`）负责实际的 Agent 执行；`schema/` 里的 A2A 协议相关 schema（`Task`、`AgentCard`、`JSONRPCRequest` 等）；`sse_starlette` 提供 SSE 支持；`fastapi` 提供 HTTP 框架

## 设计决策

**Agent Card 的静态化**：`AgentCard` 在 `__init__` 时创建，整个服务器生命周期内不变。这符合 A2A 协议的服务发现语义——`/.well-known/agent.json` 应该是稳定的元信息，不应每次请求重新生成。

**Task 存储在内存里**：`self.tasks: Dict[str, Task]` 是纯内存存储。这意味着服务器重启后任务历史丢失。注释里标注了"生产应用应使用持久存储"，但目前没有实现。大量长期 tasks 会造成内存泄漏。

**SSE 流式响应**：`tasks/sendSubscribe` 方法通过 `EventSourceResponse` 返回 SSE 流。`event_generator()` 是异步生成器，在 `AgentRuntime.run()` 的每次 yield 时发送一个 `taskArtifactUpdate` 事件（增量文本）或 `taskStatusUpdate` 事件（进度消息）。

**`metadata` 传递 agent_id/user_id**：A2A 协议本身没有身份认证字段，`agent_id` 和 `user_id` 通过消息的 `metadata` 字典传递（`message.metadata.agent_id`、`message.metadata.user_id`），缺失时使用 `"default_agent"` / `"default_user"` fallback。

## 事件收集方式

`_handle_tasks_send` 通过 `async for response in agent_runtime.run(...)` 收集所有输出，只提取有 `delta` 属性的响应片段拼接为 `final_output`。`_handle_tasks_send_subscribe` 则实时流式转发每个 delta。

## Gotcha / 边界情况

- **Task 内存泄漏**：每次请求创建的 `Task` 对象永远不会从 `self.tasks` 删除（即使任务完成）。长时间运行的服务器会持续增长。
- **`tasks/send` 是同步等待**：它会等待 `AgentRuntime` 完全执行完毕再返回，对于耗时任务客户端需要设置足够长的超时。

## 新人易踩的坑

- 试图在这里添加认证逻辑——A2A 协议目前没有内置认证，任何来源的请求都会被接受。如果需要认证，应在 `_create_app()` 里的 CORS 配置或自定义中间件里实现。
- 修改 `AgentCard` 的技能列表（`skills`）时，记得这只是服务发现元数据，不会影响 `AgentRuntime` 实际能做什么。
