---
code_file: src/xyz_agent_context/module/module_runner.py
last_verified: 2026-06-17
---

## 2026-06-17 — `_serve_one_mcp` 同端口同时挂 SSE + streamable HTTP

为让 OpenAI Codex CLI 也能用上各 Module 的 MCP 工具，`_serve_one_mcp`
从「只跑 `run_sse_async()`」改成「在同一个 port 上同时提供两套 transport」：
Claude Code 的 MCP client 只认 `/sse` + `/messages`（legacy SSE），Codex CLI
只认 `/mcp`（modern streamable HTTP），两者协议不通用。做法是把
`mcp_server.sse_app()` 与 `streamable_http_app()` 各自的 route **平铺合并**进
**一个** Starlette app（三条路径 `/sse` `/messages` `/mcp` 互不冲突），再手动用
`uvicorn.Server.serve()` 起服务。关键坑：**绝不能**用 `Mount("/mcp", ...)` 把
streamable app 挂到子路径——Starlette 会剥掉 `/mcp` 前缀转发到 `/`，而
streamable_http_app 内部只路由 `/mcp` → 404（本次修的正是这个 bug）。父 app 的
lifespan 采用 streamable 的 `lifespan_context`（它持有
`StreamableHTTPSessionManager`），sse_app 用的是 no-op 默认 lifespan 不需要链接。
注意：旧的「不要用 `FastMCP.run("sse")`、要在当前 loop 上 await」单 loop 不变式仍然成立，
只是现在 await 的是我们自己构造的 `uvicorn.Server`，而非 `run_sse_async()`。

## 2026-05-20 (Fix #2 P3) — BasicInfoModule joins CORE MCP modules (port 7808)

`BasicInfoModule` added to `CORE_MCP_MODULES` + `CORE_MODULE_PORTS` (7808) so the
mcp runner stands up its SSE server (the narrative-awareness tools — see
[[basic_info_module.py]]). 7808 = next free after CommonToolsModule(7807).
# module_runner.py — MCP 服务器部署与 A2A API 管理

## 为什么存在

`ModuleRunner` 负责把各个 Module 的 MCP 服务器变成实际运行的进程或协程。它同时管理 A2A 协议 API（基于 `chat_trigger.py`）的部署生命周期，是 `run.sh` 里 `make dev-mcp` 命令背后的实现。

## 上下游关系

- **被谁用**：`run.sh` / `Makefile` 通过 `python -m xyz_agent_context.module.module_runner mcp` 直接调用；`Tauri desktop` 通过 sidecar 启动；`backend/main.py` 在启动时可选调用
- **依赖谁**：`MODULE_MAP`（`__init__.py`）提供可用模块；`MODULE_PORTS` 字典持有各模块的固定端口；`chat_module/chat_trigger.py` 提供 A2A API Server；MCP 事件循环内 `XYZBaseModule.get_mcp_db_client()` lazy 建池

## 设计决策

**单进程单事件循环（Phase A · 2026-04-22）**：`run_mcp_servers_async()` 通过 `asyncio.gather(*[_serve_one_mcp(...) for ...])` 在**同一个 loop** 上并发跑所有 MCP 服务器。此前的"每个 module 一个 threading.Thread + 独立 loop"架构已移除。原因：aiomysql `Pool._wakeup` / `Connection._loop` 的 Future 在 pool 创建时绑到当时的 loop，多 loop 架构（thread loop + FastMCP 里 `anyio.run` 再套一个 loop）在 `asyncio.get_event_loop()` sync 路径上会把 pool 内部资源绑到错误的 loop，表现为 `got Future attached to a different loop`。单 loop 下 `get_event_loop()` 只有一个答案，根本性消除 bug family。详见 `PLAN-2026-04-22-mcp-single-loop.md` 和 `BUG_FIX_LOG.md` Bug 33。

**走 `run_sse_async` 而非 `run("sse")`**：`FastMCP.run("sse")` 内部调 `anyio.run(run_sse_async)`，anyio.run 会创建一个新的 asyncio loop，于是 thread 里 `asyncio.set_event_loop()` 设的 loop 和 anyio 跑的 loop 不是同一个——这是老架构的根因之一。`run_sse_async()` 直接在调用者的 loop 上 `await uvicorn.Server.serve()`，不套娃。

**SQLite vs MySQL 路径共用**：之前 `_is_sqlite_mode()` 决定走单进程多线程 / 多进程，单 loop 后架构不再按 backend 分叉——SQLite 和 MySQL 都走 `run_mcp_servers_async()`，SQLite 用单连接（`SQLiteBackend`），MySQL 用 aiomysql pool，两者都在同一个 loop 上，语义一致。

**端口配置分散在两处**：`MODULE_PORTS` 字典（`module_runner.py`）是文档用途，实际端口在各 Module 类的 `__init__` 里设置（如 `self.port = 7801`）。`module_runner` 里的端口数字只用于日志打印和 `_serve_one_mcp()` 的 `mcp_server.settings.port = port` 覆盖。两者必须保持一致。

**A2A API Server 通过 `chat_trigger.A2AServer` 实现**：`ModuleRunner.run_api_server()` 和 `run_module()` 都直接引用 `chat_module/chat_trigger.py`。这使得 "A2A 入口" 在名义上属于 ChatModule，但实际上是整个 AgentRuntime 的外部接口。

## Gotcha / 边界情况

- **不要把 MCP 服务器跑到 thread 里去**：这是老架构的根因（Bug 33）。`tests/module/test_module_runner_single_loop.py` 有 tripwire 测试——任何对 `threading.Thread` 的使用都会让测试失败。如果未来确实需要 thread（比如某个阻塞 API），先想清楚它和 aiomysql / FastMCP 的 loop invariant 是否冲突。
- **不要用 `FastMCP.run("sse")`**：走 sync 入口会触发 `anyio.run` 套娃。永远用 `run_sse_async()` 在当前 loop 上 await。
- **MCP 子进程绝不能同步初始化 DB**：`_run_single_mcp` 和 `ModuleRunner.__init__` 故意不调用 `get_db_client_sync()`，因为它内部用 `asyncio.run()` 建池子，`asyncio.run()` 退出时会把临时 loop 关掉，aiomysql Pool 绑在那个死 loop 上。MCP 工具里的 `await XYZBaseModule.get_mcp_db_client()` 会在 MCP 自己的 loop 里 lazy 建池，是对的路径。
- **DNS rebinding 防护要显式关闭**：FastMCP `__init__` 时如果 host 是 `127.0.0.1` 会自动开启 DNS rebinding 防护，后续改 host 到 `0.0.0.0` 不会清除这个设置。必须在 `_serve_one_mcp` 里显式 `mcp_server.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)`，否则 Docker 里 `narranexus-backend` 访问 `mcp:7803` 会被拒。

## 新人易踩的坑

- 在 `MODULE_PORTS` 里更新了端口但没有更新对应 Module 类里的 `self.port`（或反之），会导致服务器实际监听的端口与日志里打印的端口不一致。
- 运行 `module` 命令（A2A + MCP 全部署）时，A2A API Server 和所有 MCP 服务器都跑在同一台机器上，共用一个 SQLite 文件，写争用风险高。生产环境应切换到 MySQL/PostgreSQL。
- 单 loop 架构下，所有 MCP 服务器共享 CPU 线程和 aiomysql pool。某个 tool handler 如果做了 sync 阻塞调用，会拖累所有其他 MCP 服务器——这就是为什么 Bug 20 的 `with_mcp_timeout` 装饰器是必需的，任何新 MCP tool 都应该叠加。
