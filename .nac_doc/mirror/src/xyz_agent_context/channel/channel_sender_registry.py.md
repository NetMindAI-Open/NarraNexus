---
code_file: src/xyz_agent_context/channel/channel_sender_registry.py
last_verified: 2026-04-10
stub: false
---

# channel_sender_registry.py — 进程级渠道发送函数注册表

## 为什么存在

当一个 Agent 需要主动联系另一个 Agent（比如通过 `contact_agent` MCP 工具），它需要知道"我有哪些渠道可以用"以及"每个渠道的发送函数是什么"。但渠道 Module 是热插拔的，不同 Agent 可能开启了不同的渠道。

`ChannelSenderRegistry` 是一个进程级的 class-level 注册表（类变量 `_senders` 是 dict，所有实例共享），渠道 Module 在初始化时注册自己的发送函数，`contact_agent` 等复合操作通过这个注册表动态查找可用渠道，不需要硬编码依赖任何具体渠道 Module。

## 上下游关系

**被谁用**：任何需要发出渠道消息的复合工具（如 `contact_agent`），在执行时调用 `ChannelSenderRegistry.get_sender(channel)` 取得发送函数后调用。

**被谁注册**：具体渠道 Module（如 `MatrixModule`）在其 `get_mcp_config()` 或初始化时调用 `ChannelSenderRegistry.register("matrix", matrix_send_fn)`。`unregister()` 供 Module 卸载时调用（热插拔场景）。

**依赖谁**：无外部依赖，纯内存操作。

## 设计决策

使用 class-level dict（`_senders: Dict[str, SenderFunction] = {}`）而非实例变量，是为了让注册表在整个进程内全局可见，不需要传递实例引用。这是典型的 Registry 模式。

`SenderFunction` 的签名是 `async def sender(agent_id, target_id, message, **kwargs) -> dict`——设计为接受 `**kwargs` 是为了保持跨渠道的通用性（Matrix 需要 `room_id`，Slack 可能需要 `workspace`）。

## Gotcha / 边界情况

注册表是**进程级别的**——如果多进程部署（比如 Gunicorn 多 worker），每个 worker 进程各自维护独立的注册表。如果某个进程里 MatrixModule 尚未被初始化，该进程的注册表就没有 "matrix" 渠道，调用 `get_sender("matrix")` 会返回 None。

注册是"后来者覆盖"——如果同一渠道名被注册两次，第二次会静默覆盖第一次，不报错。在一个 Agent 有多个同类型渠道实例的场景（理论上不应该发生）下，后初始化的实例会赢。

## 新人易踩的坑

`available_channels()` 返回的是已注册渠道名列表，不代表这些渠道在当前对话里都可用（比如 Agent 可能有 Matrix 渠道但对方不在任何共同房间里）。注册表只管"能发"，不管"能发到谁"。

`SenderFunction` 是 `async def` 的协程函数——通过 `get_sender(channel)` 拿到函数后，调用时需要用 `await sender(...)` 而不是 `sender(...)`。
