---
code_file: src/xyz_agent_context/module/module_runner.py
last_verified: 2026-04-10
---

# module_runner.py — MCP 服务器部署与 A2A API 管理

## 为什么存在

`ModuleRunner` 负责把各个 Module 的 MCP 服务器变成实际运行的进程或线程。它同时管理 A2A 协议 API（基于 `chat_trigger.py`）的部署生命周期，是 `run.sh` 里 `make dev-mcp` 命令背后的实现。

## 上下游关系

- **被谁用**：`run.sh` / `Makefile` 通过 `python -m xyz_agent_context.module.module_runner mcp` 直接调用；`Tauri desktop` 通过 sidecar 启动；`backend/main.py` 在启动时可选调用
- **依赖谁**：`MODULE_MAP`（`__init__.py`）提供可用模块；`MODULE_PORTS` 字典持有各模块的固定端口；`chat_module/chat_trigger.py` 提供 A2A API Server；`utils/db_factory.get_db_client_sync()` 和 `get_db_client()` 按模式选择同步/异步初始化

## 设计决策

**SQLite 单进程多线程 vs MySQL 多进程**：SQLite 不支持多进程并发写，多进程模式下多个 MCP 服务器的写操作会死锁。`_is_sqlite_mode()` 检测环境变量，SQLite 下自动切换到 `run_mcp_servers_async()`（单进程内每个 MCP 服务器一个线程，共享同一个 SQLite 连接）。MySQL/PostgreSQL 则使用 `run_all_mcp_servers()`（每个模块独立进程）。

**每个线程创建新事件循环**：`_run_mcp_in_thread()` 里调用 `asyncio.new_event_loop()` 并 `set_event_loop`，因为 `fastmcp` 的 `.run(transport="sse")` 是阻塞式的，需要独立事件循环。这是绕过"一个线程只能有一个事件循环"限制的标准做法。

**端口配置分散在两处**：`MODULE_PORTS` 字典（`module_runner.py`）是文档用途，实际端口在各 Module 类的 `__init__` 里设置（如 `self.port = 7801`）。`module_runner` 里的端口数字只用于日志打印和 `_run_mcp_in_thread()` 的 `mcp_server.settings.port = port` 覆盖。两者必须保持一致。

**A2A API Server 通过 `chat_trigger.A2AServer` 实现**：`ModuleRunner.run_api_server()` 和 `run_module()` 都直接引用 `chat_module/chat_trigger.py`。这使得 "A2A 入口" 在名义上属于 ChatModule，但实际上是整个 AgentRuntime 的外部接口。

## Gotcha / 边界情况

- **Tauri sidecar 必须使用异步模式**：桌面端打包的 dmg 强制 SQLite，sidecar 启动的进程如果用多进程模式会立即死锁。`_is_sqlite_mode()` 的检测是保护机制，不要绕过它。
- **`get_db_client_sync()` 在 `__init__` 里调用**：`ModuleRunner.__init__` 同步创建 db client，但这在异步服务器里可能导致事件循环问题。`run_mcp_servers_async()` 里用 `await get_db_client()` 重新创建了一个异步连接。

## 新人易踩的坑

- 在 `MODULE_PORTS` 里更新了端口但没有更新对应 Module 类里的 `self.port`（或反之），会导致服务器实际监听的端口与日志里打印的端口不一致，排查连接问题时造成困惑。
- 运行 `module` 命令（A2A + MCP 全部署）时，A2A API Server 和所有 MCP 服务器都跑在同一台机器上，共用一个 SQLite 文件，写争用风险高。生产环境应切换到 MySQL/PostgreSQL。
