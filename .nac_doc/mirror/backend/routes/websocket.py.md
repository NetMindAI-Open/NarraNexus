---
code_file: backend/routes/websocket.py
last_verified: 2026-04-10
stub: false
---

# routes/websocket.py — Agent 运行时 WebSocket 流式通信

## 为什么存在

Agent 执行是一个需要几秒到几分钟的流式过程，期间会产生 thinking、tool call、agent response 等多种消息。HTTP 请求/响应模型无法处理这种场景，必须用长连接的流式协议。这个文件实现了 `/ws/agent/run` WebSocket 端点，是前端和 `AgentRuntime` 之间的实时通信桥梁。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(websocket_router)`（无前缀，WebSocket 路径直接是 `/ws/agent/run`）；前端聊天界面
- **依赖谁**：
  - `AgentRuntime` — 核心编排器，`async for message in runtime.run(...)` 产生流式消息
  - `CancellationToken`、`CancelledByUser` — 用户取消机制
  - `MCPRepository` — 加载 Agent 配置的 MCP URL
  - `backend.auth._is_cloud_mode`、`decode_token` — WebSocket 层的 JWT 验证
  - `backend.config.settings.ws_heartbeat_interval` — 心跳间隔（默认 15 秒）

## 设计决策

**双任务并发模式（Task A + Task B）**

WebSocket 端点同时运行两个 asyncio 任务：Task A（主任务）在 async for 循环里消费 `runtime.run()` 产生的消息并发送给客户端；Task B（`_listen_for_stop`）持续监听客户端发来的 `{"action": "stop"}` 消息。两个任务共享一个 `CancellationToken`，Task B 触发 cancel 后 Task A 的下一轮迭代会感知到并优雅退出。这个模式解决了"单向流式输出期间如何响应客户端取消信号"的问题。

**WebSocket 层做 JWT 验证而不依赖 HTTP 中间件**

浏览器 WebSocket API 不支持设置自定义 Header，所以无法用 `Authorization: Bearer ...` 传 token。中间件对 `/ws/*` 路径豁免，改由端点自己在第一条消息 payload 里读取 `token` 字段并用 `decode_token` 验证。额外做了 `token_user_id != request.user_id` 的比较，防止一个合法用户冒充另一个用户运行 agent。

**心跳任务防止代理超时**

很多反向代理（nginx、AWS ALB）在没有数据传输时会关闭空闲的 WebSocket 连接（通常 60 秒）。心跳任务每 `ws_heartbeat_interval`（默认 15 秒）发送一个 `{"type": "heartbeat"}` 消息，保持连接活跃。这对于 Agent 在思考过程中长时间没有输出的场景特别重要。

**消息序列化的多种 fallback**

`AgentRuntime` 产生的消息可能是各种类型，代码里有三种序列化尝试：`to_dict()`、`model_dump(mode='json')`、直接当 dict。这是为了兼容核心包里可能存在的不同消息类型实现。

## Gotcha / 边界情况

- **RuntimeError 吞掉而非抛出**：发送消息时如果 WebSocket 已经关闭，会抛 `RuntimeError`。代码里 catch 这个错误并 break 出循环，不是真正的异常。这是正常的连接关闭处理，不是 bug。
- **取消后 stop_listener 的清理**：即使 Agent 正常完成（不是取消），也需要 `cancel` 掉 stop_listener 任务，否则它会挂在那里等待一个永远不会到来的 stop 消息。`finally` 块里做了清理。
- **用时日志**：代码里有 `_ws_start` 和 `_step3_end` 时间戳用于日志里输出 `total` 和 `post-stream (step 4)` 耗时，`step 4` 是 Agent 发完最后一条响应后 `AgentRuntime` 继续执行的时间（比如写 Memory、更新 Narrative），这有助于分析性能瓶颈。

## 新人易踩的坑

WebSocket 连接建立后，第一条消息必须是完整的请求 payload（`agent_id`、`user_id`、`input_content` 等），不是 HTTP query param。如果前端连接后先发其他消息（比如心跳），请求解析会失败，连接会被关闭。

`/ws/ping` 是一个简单的 ping/pong 端点，只用于连接测试，不参与 AgentRuntime。
