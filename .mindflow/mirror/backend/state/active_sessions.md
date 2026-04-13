---
code_file: backend/state/active_sessions.py
last_verified: 2026-04-13
stub: false
---

# backend/state/active_sessions.py

## 为什么存在
FastAPI 进程级 WebSocket session registry。Dashboard v2 的"public agent 并发会话可见"(G002) 依赖这个数据源：传统 `chatStore.isStreaming` 只能看到当前用户的流，看不到其他用户。Registry 是唯一能回答"有几个人正在跟这个 agent 说话"的权威源。

**Not** `src/xyz_agent_context/services/` 同级的跨进程后台服务（那层是 `ModulePoller` / `InstanceSyncService`）——本文件只活在 FastAPI 进程内存，进程退出即清零。

## 上下游
- 写入：`backend/routes/websocket.py::websocket_agent_run` 在 auth 通过后、MCP/Runtime 构造前 `add`；任何异常路径（MCP 失败、AgentRuntime 崩溃、WebSocketDisconnect、CancelledError）的最外层 `finally` `remove`
- 读出：`backend/routes/dashboard.py::agents_status` 调 `snapshot()`

## 设计决策
- **Protocol 抽象**（TDR-1）：未来 WEB_CONCURRENCY>1 切 `RedisSessionRegistry` 实现同 Protocol，调用点零改
- **asyncio.Lock + snapshot 返 copy**：防止 dashboard 聚合遍历时其他协程 add/remove 触发 `dict changed size during iteration`
- **frozen dataclass SessionInfo**：避免被调用方意外修改

## Gotcha
- logging 纪律：**禁止**打印 `SessionInfo.user_id / user_display / channel`（PII）。只允许 `session_id + agent_id`
- 进程级 registry：多 worker 部署静默失败；`backend/main.py::_warn_if_multi_worker` 在 WEB_CONCURRENCY>1 时 warn
- 进程崩溃 → 所有 session 条目丢失。可接受：WS 客户端会自动重连，registry 自愈
