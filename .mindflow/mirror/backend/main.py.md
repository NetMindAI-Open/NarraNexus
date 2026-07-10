---
code_file: backend/main.py
last_verified: 2026-07-06
stub: false
---

## 2026-07-02 — billing_router 注册

新增 `from backend.routes.billing import router as billing_router` 和
`app.include_router(billing_router, prefix="/api/billing", tags=["Billing"])`。
NetMind 订阅/计费代理（[[billing]]）——`/plans`、`/subscription`。Phase 1。

## 2026-07-03 — notices router mounted at /api/notices

See backend/routes/notices.py.md.

## 2026-06-22 — narramessenger_router 注册

新增 `from backend.routes.narramessenger import router as narramessenger_router`
和 `app.include_router(...)`，挂载路径 `/api/narramessenger`（`bind` / `unbind` /
`credential`）。与 lark/slack/telegram channel 的 router 同 pattern。

## 2026-06-17 — executor idle-cull reaper 接入 lifespan

`lifespan` 启动时调 `maybe_start_executor_reaper()`(存 `app.state.executor_reaper_task`),
teardown 时 `cancel()`。云端+broker 才真正起,本地/桌面 no-op(返回 None)。
回收空闲 per-user executor 容器,只碰空闲的(铁律 #14)。见
`[[../src/xyz_agent_context/agent_runtime/executor_reaper.py]]`。

## 2026-06-12 — admin_migration_router 注册

新增 `from backend.routes.admin_migration import router as admin_migration_router` 和对应的 `app.include_router(admin_migration_router, tags=["AdminMigration"])`。router 自带 prefix `/api/admin`，最终挂载路径为 `POST /api/admin/migrate-identity`。与 `admin_quota_router` 同 pattern（自带 prefix，`include_router` 不再传 prefix 参数）。

## 2026-06-11 — invite routers unwired

invite_router / admin_invite_router imports and include_router calls removed alongside the route modules' deletion (invite-code mechanism retired).

last_verified: 2026-06-09
stub: false
---

## 2026-06-09 — versioned migration runner wired into lifespan

The startup lifespan now calls `run_pending_migrations(db)` after `auto_migrate`
and the one-shot self-heals — the universal hook for the versioned data-migration
ledger (migrations/ [[__init__]]). Wrapped defensively (best-effort): a migration
error is logged and never blocks startup. This is what carries the
unified-memory backfill to EVERY environment (cloud / run.sh / DMG) without a
deploy-side step.

## 2026-06-08 — analytics shutdown wired into lifespan

`lifespan` teardown now calls `await shutdown_analytics()` (from
`xyz_agent_context.analytics`) just before `close_db_client`. This drains the
PostHog background-thread buffer so no buffered funnel events are lost on
process exit.

## 2026-05-15 — invite_router 改为 server-to-server

挂载点没变(`/api/invite`),但其下的路由从公开的 `/request` 改成内部的
`/internal/issue`(server-to-server,X-Internal-Secret header 鉴权)。
详见 `backend/routes/invite.py` mirror md。`admin_invite_router` 不变。

CORS 那行 `narra.nexus` 域名其实现在用不到了(website 调 NarraNexus 走
server-to-server,不是浏览器跨域),但留着无害。

## 2026-05-14 — invite routers wired in

新增两个 router:`invite_router`(`/api/invite`)和 `admin_invite_router`
(自带 `/api/admin/invite` 前缀,staff 专用,故 `include_router` 不再传
prefix——与 `admin_quota_router` 同 pattern)。

## 2026-05-13 — Phase C: active_runs registry + reconcile

`lifespan` 启动时初始化 `app.state.active_runs = {}` ——
WS handler 和 BackgroundRun 共同使用的 in-memory map（`run_id` →
BackgroundRun 对象）。

之后跑 reconcile：UPDATE events SET state='failed' WHERE state='running'。
理由：进程刚启动 active_runs 必空，任何 events.state=='running' 的行
都是上次进程留下的孤儿 task。flip 成 `failed` 防止前端 polling 时
误显示"还在跑"。`error_message='backend restarted, run lost'`。

reconcile 必须在 backend 接受任何 WS 请求之前完成。所以放在 lifespan
入口，紧跟 auto_migrate + provider_driver backfill 之后。

## 2026-05-13 — Provider Unification boot wiring

`lifespan` now calls `provider_driver.backfill_provider_metadata(db)` right
after `auto_migrate`. Idempotent; fills the four new `user_providers`
columns on legacy rows so the unified resolver works on first boot after
upgrade. Also registers `notifications_router` (`/api/notifications/*`)
so the self-heal mechanism's notification feed has a public surface.

See `reference/self_notebook/specs/2026-05-13-provider-unification-design.md`.

## 2026-05-08-r3 simplification — artifact_ws_router removed

`artifact_ws_router` (added 2026-05-08) has been removed from `main.py`.
The in-process `ArtifactEventBus` / `artifact_ws.py` notification path was
dropped entirely because the bus lived in the MCP server process while the
`/ws/artifacts/{agent_id}` subscribers lived in the FastAPI process — cross-
process `publish()` never delivered events. The frontend already receives
artifact signals through the existing chat WebSocket stream (`tool_output`
frames parsed in `ChatPanel.tsx`). One signal path is simpler and correct.

The import line `from backend.routes.artifact_ws import router as artifact_ws_router`
and the corresponding `app.include_router(artifact_ws_router, tags=["Artifacts"])` call
were removed. All other routers are unchanged.

## 2026-05-08 addition — agents_artifacts router wire-in

`agents_artifacts_router` (from `backend.routes.agents_artifacts`) is now
imported and registered at `/api/agents` with `["Artifacts"]` tags. This
router provides CRUD endpoints for artifact management:

- `GET /api/agents/{agent_id}/artifacts` — list artifacts in a session
- `GET /api/agents/{agent_id}/artifacts/{artifact_id}` — fetch artifact detail with versions
- `PATCH /api/agents/{agent_id}/artifacts/{artifact_id}` — pin/unpin artifacts
- `DELETE /api/agents/{agent_id}/artifacts/{artifact_id}` — delete artifact and cleanup
- `GET /api/agents/{agent_id}/artifacts/{artifact_id}/v{version}/raw` — raw artifact content with strict CSP

All routes enforce agent isolation via the `agents_artifacts_auth_required`
dependency, preventing agents from accessing other agents' artifacts.

## 2026-04-28 addition — unified logging + access middleware + admin logs router

`lifespan()` now calls `setup_logging("backend")` as its very first
action so every line emitted from FastAPI startup, schema migration,
quota wiring, and route handlers flows through the same loguru sinks
(stderr + `~/.narranexus/logs/backend/backend_YYYYMMDD.log`). At
shutdown an `await logger.complete()` flushes the multiprocessing
queue used by `enqueue=True` so the final lines describing the
shutdown actually survive.

The custom `logger.remove()` + `logger.add(sys.stderr, ...)` block at
module top is gone; that responsibility now lives entirely inside
`setup_logging`. See `src/xyz_agent_context/utils/logging/` for the
public surface.

A new HTTP middleware is registered alongside `auth_middleware`:
`backend.middleware.access_log.access_log_middleware`. Order matters
— FastAPI runs middleware LIFO, so we register `auth_middleware`
FIRST and `access_log_middleware` SECOND, which means access_log
wraps auth and 401 / 402 responses still produce one access line.

A new admin-only router is mounted at `/api/admin/logs` (see
`backend.routes.admin_logs`). It surfaces the on-disk
`~/.narranexus/logs/<service>/` tree over HTTP so cloud operators
can tail / download / event-grep without ssh. The prefix already sits
under `QUOTA_BYPASS_PREFIXES` in `auth.py`, so it is unauthenticated
in local mode and JWT-gated in cloud mode.

## 2026-04-16 addition — system-default quota wiring

Lifespan now constructs the four quota-feature dependencies after
`auto_migrate` and binds them to `app.state`:

- `app.state.system_provider` — `SystemProviderService.instance()` (module
  singleton; reads env once)
- `app.state.quota_service` — `QuotaService(QuotaRepository(db),
  system_provider)`, also registered as `QuotaService.set_default()` so
  `cost_tracker.record_cost`'s deduct hook can reach it
- `app.state.user_repository` — `UserRepository(db)` used by the admin
  endpoints to validate target users
- `app.state.provider_resolver` — `ProviderResolver(user_provider_svc,
  system_provider, quota_service)` called by `auth_middleware` on every
  authenticated cloud-mode request

Two new routers (`quota_router` at `/api/quota/me`, `admin_quota_router`
at `/api/admin/quota/*`) are included alongside the existing set. Local
mode uses the same code paths but every gated service returns no-op
values, so lifespan wiring is harmless when the feature is off.

# main.py — FastAPI application entry point

## 为什么存在

`main.py` 是整个后端的根节点，负责把所有零散的路由、中间件、数据库初始化组装成一个可运行的 ASGI 应用。它还承担了一件非常重要的职责：在同一个进程里同时服务 API 和前端静态文件，这样打包进 Tauri dmg 的时候只需要启动一个进程，而不是两个。

## 上下游关系

- **被谁用**：uvicorn 直接引用 `backend.main:app`；Tauri sidecar 通过 `run.sh` 或打包后的可执行文件启动同一个入口
- **依赖谁**：
  - `backend.config.settings` — 读取 CORS origins 和 frontend_dist 路径
  - `backend.auth.auth_middleware` — 注入 HTTP 鉴权中间件
  - `xyz_agent_context.utils.db_factory` — `get_db_client` / `close_db_client` 管理连接池生命周期
  - `xyz_agent_context.utils.schema_registry.auto_migrate` — 启动时执行表结构迁移
  - 全部路由模块：`websocket`, `agents`, `jobs`, `auth`, `skills`, `providers`, `inbox`

## 设计决策

**中间件注册顺序（LIFO 陷阱）**

FastAPI/Starlette 的中间件以 LIFO（后进先出）顺序执行，即最后注册的中间件最先处理请求。目前的注册顺序是：先注册 `CORSMiddleware`，再通过 `app.middleware("http")` 注册 `auth_middleware`。结果是 `auth_middleware` 实际上在 CORS 之前运行。这意味着浏览器的 CORS preflight（OPTIONS）请求会先进入 `auth_middleware`，如果不在那里做特殊处理，就会被 401 拦截，CORS 头永远不会被加上。因此 `auth_middleware` 内部有一段硬编码的 `if request.method == "OPTIONS": return await call_next(request)` 来放行 preflight，把控制权还给 CORS 中间件。

这是一个被动防御方案——不改变注册顺序，而是在 auth 里主动放行。如果将来在 auth 和 CORS 之间插入新的中间件，必须同样考虑 OPTIONS 放行。

**lifespan 而非 startup/shutdown 事件**

旧版 FastAPI 用 `@app.on_event("startup")` / `@app.on_event("shutdown")`，新版推荐 `asynccontextmanager` 的 `lifespan` 参数。这里选择新版做法，好处是数据库连接的初始化和清理代码放在同一个函数里，语义更清晰，也不会忘记配对。

**前端静态文件的条件挂载**

如果 `frontend/dist/index.html` 存在，就挂载 `/assets` 静态目录并添加 SPA fallback 路由；否则只暴露一个 `GET /` 健康检查。这让同一套代码既能作为纯 API 服务（开发时前端跑在 Vite 单独进程），也能在生产/dmg 模式下直接服务打包后的前端。SPA fallback 是 catch-all `/{full_path:path}`，必须在所有 API 路由之后注册，否则会劫持 `/api/*` 路径。

**schema auto_migrate**

启动时调用 `auto_migrate(db._backend)` 自动执行建表/加列。这个函数对 SQLite 和 MySQL 都能工作，但它直接访问了 `AsyncDatabaseClient` 的 `_backend` 私有属性，算是一个轻微的封装泄露。如果将来 db_factory 的内部结构调整，这里需要同步更新。

## Gotcha / 边界情况

- **OPTIONS 请求必须在 auth 中手动放行**：见上文 LIFO 陷阱。任何新增的 HTTP 中间件，如果需要对所有请求生效，都必须同样放行 OPTIONS，否则跨域调用全部失败，症状是浏览器报 CORS error 但服务器日志里看到的是 401。
- **SPA fallback 的路由顺序**：前端挂载代码在 `main.py` 底部，必须在所有 `app.include_router(...)` 之后执行。如果新增路由但忘记在前端挂载代码之前注册，SPA fallback 会先匹配到新路径并返回 `index.html`，导致 API 调用失效。
- **`auto_migrate` 访问私有属性**：`db._backend` 是私有字段，重构 `AsyncDatabaseClient` 时需要检查这里。

## 新人易踩的坑

直接改中间件注册顺序（比如把 CORSMiddleware 移到 auth_middleware 之后）会修复"CORS 先执行"的直觉期望，但如果同时删掉 `auth_middleware` 里的 OPTIONS 放行逻辑，结果是一样的——auth 先跑，preflight 被 401。两个地方必须同步考虑。

在 `lifespan` 里 yield 之后报错（比如 `close_db_client` 抛出异常），uvicorn 会打印错误但不会阻止进程退出，这是正常的关闭行为，不是 bug。
