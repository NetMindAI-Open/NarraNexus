---
code_file: src/narranexus/platform/module_system/module_runner.py
last_verified: 2026-09-23
---

## 2026-09-22 - Optional browser stream mount

The host mounts the browser stream bridge only when `browser_module` is in the
actual mounted MCP server list. A disabled or unselected browser module causes
no browser import, service creation or stream route. When enabled, bridge
construction errors fail visibly instead of being swallowed as "disabled".
The lookup uses the same process-wide service as browser tools. Enabled host
shutdown closes that service before releasing the module transport lifespans,
so queued actions and browser processes are drained. The backend
proxies that internal stream; frontend and backend gating belong to their
respective owners. Existing host transport, identity and lifespan behavior is
preserved.

2026-09-23：内部 stream 同时接入共享 BrowserService.login_control_changed。
实际接管/显式交还/断线回调由持有会话的 MCP 进程驱动持久登录通知，不让后端凭
前端自报状态判定完成；可选模块关闭时也不创建该回调或服务。

## 2026-09-09 — `run_mcp_servers_async` 关闭它自己开的池（B-41；复审 I5/M5 修订）

单进程/本地或直连 MySQL 模式下,这个协程用 `get_db_client()` 在**自己这个 loop** 上开了池
(`aiomysql.Pool` 把 Future 绑死在建它的 loop 上,见 2026-08-11 那条 entry)。2026-07-27
补的集中式优雅退出只在 `finally` 里摘信号处理器,从没关过这个池——`server.serve()` 一返回,
外层 `asyncio.run()` 就把 loop 拆了,池还开着。之后 GC 才终结那些遗留的连接对象,而这时 loop
已经关了,于是每条连接各打一次 `"Event loop is closed"` 警告(dev 日志里连续 11 条,对应
`aiomysql` 的 `minsize=1..maxsize` 池里那几条连接)。

第一版只在 `serve()` 的 `finally` 里关池，复审指出两条**启动失败**出口（`if not instances:
return`、`auto_migrate` 抛异常）都在那个 try 之前，池照样漏——恰恰是最需要看清日志的时候。
现在结构是：`_open_pool_unless_seamed()` 在 try **外**开池（开失败没东西可关）；`auto_migrate`
+ `_serve_host()` 在 try **内**；唯一的 `finally` 调 `_release_host_resources(db)`。测试
`test_a_failing_auto_migrate_still_closes_the_pool_and_propagates` 抓到过一版把 migrate 放
进 opener 里的写法（异常时 `db` 未绑定 → 不关）。

`_release_host_resources` 的顺序抄两个同族入口（`run_worker_supervisor._drain_and_close`
/ `run_channel_triggers.main`）的纪律——**先 join 工作，再关池，最后 flush 日志**：
1. `utils.background_tasks.spawn` 派出的脱离任务（hook_manager 会拉起一整个 run、dataloader
   批刷、narrative updater）若落在关池之后会撞 `_backend=None`。`drain(timeout=_BACKGROUND_DRAIN_SEC)` →
   剩余 cancel → gather(return_exceptions)，与先例的 stop→cancel→gather 三段式同形；有上限
   是因为进程反正在退出，卡死的任务不能把关停拖过 stop grace。
   **关停预算（复审 PR#393 I1）**：drain 不能吃满 grace，否则恰好在"有在途任务"这个唯一需要本修复
   的场景里，进程在 close_db_client() 之前就被 SIGKILL。各监管方给的 SIGTERM→SIGKILL 窗口：
   Tauri `process_manager.rs::stop_service` 3s（最紧）；deploy 仓 compose 的 `mcp` 服务未设
   `stop_grace_period` → Docker 默认 10s；`run.sh` 的清理直接 `kill -9` 端口，本来就没有 grace。
   因此 `_STOP_GRACE_BUDGET_SEC = 3.0`、`_POOL_CLOSE_HEADROOM_SEC = 1.0`，
   `_BACKGROUND_DRAIN_SEC = 3.0 - 1.0 = 2.0s` 由二者推导而不单独设值——第一版写成 10.0 恰等于
   Docker 默认 grace。`test_a_wedged_detached_task_still_leaves_time_to_close_the_pool` 用一个
   永不结束的任务钉住：任务被 cancel、池照样关、全程 < 3s（改回 10.0 即红）。
2. `db is not None` 才 `close_db_client()`（seam=HttpStore 模式没开过池，
   `test_sigterm_does_not_close_a_pool_it_never_opened` 钉住）。
3. `await logger.complete()`——loop 关掉之后再 flush 的 sink 会丢关停的最后几行。
`test_detached_work_is_joined_before_the_pool_closes` 钉住 1→2 的顺序；
`test_the_no_servers_early_return_still_closes_the_pool` 钉住早退出口。GC 之后才冒出来的
"Event loop is closed" 警告本身在单元测试里没法确定性复现（GC 时机不可控），手工验证仍需
在 dev/MySQL 环境跑一次 `docker compose restart mcp`，确认日志里不再冒出那 11 条警告。

## 2026-09-08（复审）— boot 提到 `main()` 顶部

`module` / `list` / usage 同样读注册表派生的模块表，只在 `run_mcp_servers_async` 里 boot 不够；`main()` 起手 boot 一次
（幂等，程序化调用者再 boot 是 no-op），`run_module` 不再自行解析模块而是把 `modules` 原样传下去。

## 2026-09-08（本地 E2E 实测）— mcp 角色先 boot 再解析模块

`run_mcp_servers_async` 此前先 `_resolve_modules` 再 `boot_mcp_plugins()`；批 6 起模块表由注册表在 boot 时派生，
于是 `module_runner mcp` 在 run.sh / Tauri / compose 下一律「No modules to run」退出，整个 MCP 宿主没起来
（单测里 boot 被 monkeypatch 成 no-op、模块列表直接注入，所以没抓到）。现在 boot 先行；新测试让「boot 前解析为空、
boot 后有模块」并断言宿主真的起来。

## 2026-09-04（批 3c.1）— 端口表从贡献表派生

`CORE_MCP_MODULES`/`CORE_MODULE_PORTS` 由 `MODULE_SPECS` 计算（名字保留：port preflight 测试与调用方 import 它们）；
`all_mcp_modules()`/`all_module_ports()` 只列 module_registry 里还在的（禁用感知）。三条直接类 import 删除。

## 2026-09-03（批 2f.1）— `NexusPluginsModule` 进 `CORE_MCP_MODULES`，端口 7811

## 2026-09-03（批 2b.5）— async 模式起 MCP 前 `boot_mcp_plugins()`

让模块 server 看到插件贡献的 tools / mcp servers / skills。

## 2026-08-11 — 单进程判据从「是否 sqlite」改成「是否持 MySQL 池」；async runner 无 seam 时零凭据

`_is_sqlite_mode` → **`_is_single_process_mode`**：`NARRANEXUS_BACKEND_URL` 置位（seam=HttpStore，`_seam_uses_backend()`）**或** sqlite/空 → 单进程 async（`run_mcp_servers_async`）。原来只看 DATABASE_URL 空不空——空判成 sqlite，纯属巧合，且语义错位（云端 seam 进程不是 sqlite）。动机是**内存**：~17 个模块服务器各起一进程要各 `import xyz_agent_context`（~260MB/进程 → 数 GB），单进程只付一次；多进程只在「每进程自带 MySQL 池」（无 seam 的直连云端）时才值得。本地 dev（不设 BACKEND_URL）走老 sqlite 判断，不变。

**承重（预审 Important）**：`run_mcp_servers_async` 原来**无条件** `get_db_client()` + `auto_migrate()` + 注入 `database_client=db`——那样单进程 seam 模式并非零凭据（strip DATABASE_URL 会开机崩），且会让云端 mcp 从多进程翻单进程后**在 mcp 容器里跑 DDL**（多进程路 `_run_single_mcp` 用 `database_client=None`、不迁移）。改为：`_seam_uses_backend()` 为真时**跳过 get_db_client+auto_migrate、`database_client=None`**（同多进程路，工具全走 HttpStore，迁移归 backend）；仅 sqlite/local 才建池+迁移（aiomysql 循环绑定）。守卫测试见 [[test_module_runner_single_loop]]（`test_async_runner_is_credfree_when_seam_is_httpstore` + 三个模式判据用例）。

## 2026-08-10 — wrapped app 佩戴 IdentityAuthMiddleware（MCP caller auth）

`_build_mcp_server` 合并两套 transport 的那个 Starlette app 是**唯一咽喉**,
中间件挂在这里就覆盖全部模块、全部 transport,零 per-module 步骤。行为由
`NX_MCP_AUTH_MODE` 决定(默认 off = 严格 no-op),见 [[identity/mcp_auth]]。
守卫测试:`test_build_mcp_server_installs_identity_auth_middleware`。

## 2026-08-01 — 两个部署点改走 build_instrumented_mcp_server

`create_mcp_server()` → `module.build_instrumented_mcp_server()`(两处:
同步 `start_mcp_server` 与 async 多路服务)。原因见 [[base]] / [[_mcp_identity]]:
调用者身份注入必须覆盖所有被服务的模块。**刻意保持严格**(不做
`getattr` 回落):绕过包装的模块会被以"信任模型填的 agent_id"这个旧 bug
行为服务,那是静默降级,宁可让它显式炸。测试里的 duck-typed 假模块因此
同批补上了该方法。

## 2026-07-27 — 单进程 MCP 的集中式 SIGTERM/SIGINT 优雅退出（修孤儿占端口）

`run_mcp_servers_async`（桌面 SQLite 模式 + dev `pkill` 路径的实际入口）此前
把每个 `uvicorn.Server` 直接 `gather(server.serve())`。uvicorn 0.38 的
`serve()` 用 `capture_signals()` → `signal.signal(SIGTERM/SIGINT, handle_exit)`
注册**进程级**信号处置；同一 loop 上 N 个 server 并发进入时**最后一个覆盖前面
所有**，于是一个 SIGTERM 只让最后那个 server `should_exit`，其余 N-1 继续
`serve()` 占着 MCP 端口，主进程不自行退出（hang 到被外部 SIGKILL），下次启动
`[Errno 48] address already in use`（桌面版表现为「power 登录 load failed / 重开
提示端口占用」）。

修法：
- 新增模块级 `_no_signal_capture`（no-op contextmanager），在 `_build_mcp_server`
  里 `server.capture_signals = _no_signal_capture` **中和**每个 server 自带的
  信号捕获。
- `_serve_one_mcp` 拆成 `_build_mcp_server`（构造 uvicorn.Server，不 serve）+ 薄
  serve 封装；`run_mcp_servers_async` 直接持有 server 引用列表。
- `run_mcp_servers_async` 用 `loop.add_signal_handler(SIGINT/SIGTERM, …)` 装
  **唯一**集中 handler，触发时把**所有** server `should_exit=True` → 全部
  `serve()` 返回 → `asyncio.run()` 干净退栈 → 释放所有 MCP 端口；`finally` 里
  `remove_signal_handler` 清理。对齐 `run_worker_supervisor` 的 SIGINT+SIGTERM
  模式（铁律 #7：`run.sh`/dev `pkill` 与桌面退出行为一致）。

Gotcha：**别再让 uvicorn 各 server 自己抢信号**——多 server 单进程时必经
`_no_signal_capture` 中和，否则集中 handler 会被 uvicorn 的 `signal.signal`
覆盖回去。回归守卫见 `test_sigterm_stops_every_server_not_just_the_last`
（3 个 server，一次 SIGTERM 必须全退）+
`test_build_mcp_server_neutralises_uvicorn_signal_capture`。

**已知相邻问题（未在本次处理）**：MySQL/cloud 路径 `run_all_mcp_servers` /
`run_module` 仍只在 `except KeyboardInterrupt`（SIGINT）里 `terminate()` 其
multiprocessing 子进程，收到 SIGTERM 会漏杀子进程。云端跑在 Docker/独立容器里，
容器销毁会连带回收，危害远低于共享主机上的桌面场景，故本次仅记录。

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

2026-07-13：`uvicorn.Config` 必须传 `log_config=None`。默认 dictConfig 会把
`uvicorn.*` logger 设成 propagate=False + 自带 stderr handler，等于把它们从
loguru InterceptHandler 桥（`utils/logging/_intercept.py` 的"一套日志"设计）里
静默拽走；`log_level` 参数不受影响照常生效。该坑最先以测试顺序污染形态暴露
（构造过 Config 后 `test_noisy_logger_clamped_to_warning` 必挂）。

## 2026-05-20 (Fix #2 P3) — BasicInfoModule joins CORE MCP modules (port 7808)

`BasicInfoModule` added to `CORE_MCP_MODULES` + `CORE_MODULE_PORTS` (7808) so the
mcp runner stands up its SSE server (the narrative-awareness tools — see
[[basic_info_module.py]]). 7808 = next free after CommonToolsModule(7807).
# module_runner.py — MCP 服务器部署与 A2A API 管理

## 为什么存在

`ModuleRunner` 负责把各个 Module 的 MCP 服务器变成实际运行的进程或协程。它同时管理 A2A 协议 API（基于 `chat_trigger.py`）的部署生命周期，是 `run.sh` 里 `make dev-mcp` 命令背后的实现。

## 上下游关系

- **被谁用**：`run.sh` / `Makefile` 通过 `python -m narranexus.platform.module_system.module_runner mcp` 直接调用；`Tauri desktop` 通过 sidecar 启动；`backend/main.py` 在启动时可选调用
- **依赖谁**：`module_registry`（`__init__.py`）提供可用模块；`MODULE_PORTS` 字典持有各模块的固定端口；`chat_module/chat_trigger.py` 提供 A2A API Server；MCP 事件循环内 `XYZBaseModule.get_mcp_db_client()` lazy 建池

## 设计决策

**单进程单事件循环（Phase A · 2026-04-22）**：`run_mcp_servers_async()` 通过 `asyncio.gather(*[_serve_one_mcp(...) for ...])` 在**同一个 loop** 上并发跑所有 MCP 服务器。此前的"每个 module 一个 threading.Thread + 独立 loop"架构已移除。原因：aiomysql `Pool._wakeup` / `Connection._loop` 的 Future 在 pool 创建时绑到当时的 loop，多 loop 架构（thread loop + FastMCP 里 `anyio.run` 再套一个 loop）在 `asyncio.get_event_loop()` sync 路径上会把 pool 内部资源绑到错误的 loop，表现为 `got Future attached to a different loop`。单 loop 下 `get_event_loop()` 只有一个答案，根本性消除 bug family。详见 `PLAN-2026-04-22-mcp-single-loop.md` 和 `BUG_FIX_LOG.md` Bug 33。

**走 `run_sse_async` 而非 `run("sse")`**：`FastMCP.run("sse")` 内部调 `anyio.run(run_sse_async)`，anyio.run 会创建一个新的 asyncio loop，于是 thread 里 `asyncio.set_event_loop()` 设的 loop 和 anyio 跑的 loop 不是同一个——这是老架构的根因之一。`run_sse_async()` 直接在调用者的 loop 上 `await uvicorn.Server.serve()`，不套娃。

**进程拓扑：单进程 iff 本进程不持 MySQL 池**（`_is_single_process_mode()`，2026-08-11）：seam=HttpStore（`NARRANEXUS_BACKEND_URL` 置位，`_seam_uses_backend()` → 无池、跳 `auto_migrate`、`database_client=None`）**或** SQLite/空 `DATABASE_URL` → 单进程 `run_mcp_servers_async()`（所有 MCP 服务器同一 loop，SQLite 用 `SQLiteBackend`）；**直连 MySQL（无 seam）→ `run_all_mcp_servers()` 多进程**，每进程自带 aiomysql pool。即拓扑**按「本进程是否持池」分叉**，不是「都走单进程」。动机是内存：~17 个模块服务器各起一进程要各 `import xyz_agent_context`（~260MB/进程 → 数 GB），单进程只付一次。（旧代码曾按 `DATABASE_URL` 是否 sqlite 判断——那只是历史巧合，seam 进程并非 sqlite。）

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

## 2026-09-04 · ingress triggers (batch 3c.3)

`_a2a_server_class()` resolves the A2A server through `ingress.triggers` (`a2a`, host=api, provided by builtin.chat) — the runner no longer imports `chat_module.chat_trigger`; with builtin.chat disabled `run_api_server`/`run_module` fail loud with a clear message.

## 2026-09-04 · one MCP host, every module mounted by path (batch 5a)

`run_mcp_servers_async` builds ONE uvicorn server (`_build_host_server`) on `MCP_PORT` (`module/base.py mcp_port()`, default 7801): a Starlette host app that mounts each module's dual-transport app (`_build_module_app`: FastMCP `sse_app` routes for Claude Code + `streamable_http_app` routes for Codex, the streamable lifespan kept) at `/mcp/<server_name>` — the `server_name` the module advertises in its `MCPServerConfig` — plus `GET /mcp/healthz` (mounted servers) and the caller-identity middleware once at the host. The host lifespan enters every mounted app's lifespan (Starlette does not run a Mount's). The SSE transport is mount-aware (`root_path`), which is what lets one port serve every module. Gone: `CORE_MODULE_PORTS`, `MODULE_PORTS`, `all_module_ports`, the port half of `discover_channel_modules`, the multiprocessing modes (`run_all_mcp_servers`, `_run_single_mcp`, `run_mcp_server`) and `_is_single_process_mode` — single loop was already the production shape and the reason per-process ports existed is gone. `run_module` runs the A2A API process + the host on its own loop. Shutdown: one SIGINT/SIGTERM handler stops the host (the port always releases).

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

## 2026-09-04 · `main(argv)` (batch 6a)

The CLI block became `main(argv=None) -> int` so the one-release `xyz_agent_context` path shim (and any launcher) can call it; the moved module is `narranexus.platform.module_system.module_runner`.

## 2026-09-07 — all_mcp_modules derives from module_registry at call time

The MCP host used to mount CORE_MCP_MODULES (the platform's builtin table) plus ChannelModuleBase subclasses, computed at import — a plugin-contributed module with tools was never served and its tool calls 404'd silently. all_mcp_modules() now lists every module the registry knows when called (plugin modules included; the runner already skips a module whose mcp_server() is None, now at info level); the import-time DEFAULT_MCP_MODULES constant is gone.

## 2026-09-07 — no CORE_MCP_MODULES table

Core vs channel modules are told apart by the contribution meta the plugin declared, read from the registry at call time.


## 2026-09-07 — marks the plugin boot healthy only once it serves (round-2 🟡-6)

The plugin boot no longer declares itself healthy on its last line; this
entrypoint calls `plugins_boot.mark_host_healthy(<role>)` after the process is
actually serving, which is the only moment that may clear the boot-crash marker
and move the last-known-good registry snapshot. Reaching the boot and then dying
must count as a failed boot.
