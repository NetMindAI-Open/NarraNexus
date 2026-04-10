---
code_file: backend/main.py
last_verified: 2026-04-10
stub: false
---

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
