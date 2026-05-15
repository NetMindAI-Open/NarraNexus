---
code_file: backend/auth.py
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — Local 模式多用户支持（X-User-Id header）

之前 `get_local_user_id()` 用 `db.get_one("users", {})` 拿"users 表
第一行"作为 local 模式唯一用户——TDR-12 当时假设 local 模式只跑单
用户。但本地版实际上是有 user 登录系统的多用户场景：两个真实用户
在同一台机器上登录、各自管理自己的 agent / team / dashboard。原
singleton 行为让所有人共享同一个 `local-default` 身份 → teams /
dashboard / agents_cost / bundle 全部串号。

修复思路：让 cloud 和 local 走**同一条 identity 通路** ——
`request.state.user_id` 是统一出口。差异只在 middleware 内：

- cloud 模式（原有）：JWT Bearer → 验签 → 写 state.user_id
- local 模式（新）：`X-User-Id` HTTP header（前端从 configStore
  注入）→ 直接信任 → 写 state.user_id。没 header 时 fallback 到
  `get_local_user_id()` 的 singleton（bootstrap / 老前端兼容）

新 helper `resolve_current_user_id(request)` 是路由层唯一入口。
所有 route handler 调它就行，**不再有 `if _is_cloud_mode()` 分支**——
这是关键合规点：cloud 多租户隔离逻辑和 local 多用户隔离逻辑跑同
一份下游代码，行为完全一致。

local 模式 middleware 现在也调
`set_current_user_id(request.state.user_id)` ContextVar——之前只有
cloud 调，导致 local 模式 cost_tracker 归属丢失（bonus 修复，
跟主目标无关但顺手）。

**安全模型**：
- cloud：JWT 签名保证身份不可伪造
- local：OS user 就是 security boundary（在你机器上跑 backend
  的人本来就能读你所有数据），X-User-Id 不需要签名；spoofing 也
  spoof 不到任何 cloud 用户（cloud middleware 走 JWT 路径完全不读
  这个 header）

`get_local_user_id()` 保留——middleware 在 header 缺失时仍调用它做
fallback；不再是路由层的"权威 source"，docstring 已经更新。

## 2026-04-16 addition — system-default quota routing

`auth_middleware` now, after the JWT has been decoded and
`request.state.user_id` / `role` are populated:

1. Sets the `current_user_id` ContextVar (consumed by
   `cost_tracker.record_cost` to attribute token usage without wide
   parameter threading).
2. Invokes `app.state.provider_resolver.resolve_and_set(user_id)` to
   decide whether the request should consume the user's own provider
   config or fall back to the system-default NetMind key, with quota
   gating. The resolver itself short-circuits when the feature is
   disabled (local mode / env off), so this path is transparent.
3. Catches `QuotaExceededError` and emits HTTP 402 with
   `error_code: QUOTA_EXCEEDED_NO_USER_PROVIDER`. The frontend
   interceptor pattern-matches the code, not the message, and
   surfaces a toast directing the user to configure their own
   provider.

# auth.py — JWT 认证工具与 HTTP 中间件

## 为什么存在

系统需要同时支持两种运行模式：本地桌面模式（SQLite，单用户，无需登录）和云端多租户模式（MySQL，多用户，需要密码和 JWT）。`auth.py` 把这两种模式的差异集中在一个地方处理，让路由层完全不感知模式切换。它提供密码哈希、JWT 生成/验证，以及一个 HTTP 中间件，让云模式下所有非豁免的 `/api/*` 路径都强制要求有效 token。

## 上下游关系

- **被谁用**：
  - `backend/main.py` — 注册 `auth_middleware` 作为全局 HTTP 中间件
  - `backend/routes/auth.py` — 调用 `hash_password`, `verify_password`, `create_token`, `_is_cloud_mode`, `INVITE_CODE`
  - `backend/routes/websocket.py` — 调用 `_is_cloud_mode`, `decode_token`（WebSocket 无法用 HTTP 头传 token，所以 WS 端自己验证）
  - `backend/routes/providers.py` — 通过 `request.state.user_id` 读取中间件注入的用户信息
- **依赖谁**：
  - `bcrypt` — 密码哈希
  - `PyJWT`（`jwt`）— token 生成和验证
  - 运行时读取 `DATABASE_URL`（或 fallback 到 `DB_HOST`）、`JWT_SECRET`、`INVITE_CODE` 环境变量

## 设计决策

**`_is_cloud_mode` 的安全默认值**

判断是否为云模式时，优先检查 `DATABASE_URL`，若为空则 fallback 检查 `DB_HOST`（与 `database.py` 的 `load_db_config()` 对齐）。两者都为空时视为本地模式。这个决策是为了修复 Tauri dmg 打包后的一个具体 bug：macOS 上 Rust 通过 `std::env::set_var` 设置环境变量不是线程安全的，tokio 生成的 Python 子进程可能无法读到它。如果默认云模式，没有 `DATABASE_URL` 的桌面用户每次启动都会被要求输入密码，完全破坏本地使用场景。被否决的方案是用独立的 `MODE=cloud/local` 环境变量，但这需要两处配置同步，容易出现 `MODE=cloud` 但 `DATABASE_URL` 指向 SQLite 的矛盾状态。

**OPTIONS 请求豁免**

`auth_middleware` 在所有逻辑之前先检查 `request.method == "OPTIONS"`，如果是就直接 `call_next`。原因是 FastAPI 中间件以 LIFO 顺序执行，`auth_middleware` 注册晚于 `CORSMiddleware`，实际上比 CORS 先运行。浏览器的 CORS preflight 不携带 `Authorization` 头，如果不在这里放行，preflight 会被 401，CORS 头永远不会被添加，前端所有跨域请求都会失败。

**WebSocket 的 token 传递方式**

浏览器 WebSocket API 不允许设置自定义 Header，所以 WS 连接无法通过 `Authorization: Bearer ...` 传 token。中间件豁免 `/ws/*` 前缀，让 WebSocket 端点自己在第一条消息的 payload 里接收 `token` 字段并调用 `decode_token` 验证，同时比较 `token_user_id` 和 payload 里的 `user_id`，防止一个合法用户冒充另一个用户运行 agent。

**`require_auth` 函数是空壳**

代码里有一个 `require_auth` 函数但实现是 `pass`，注释说"通过中间件处理"。这是历史遗留——最初打算用 `Depends(require_auth)` 做路由级鉴权，后来改为全局中间件方案后这个函数成了死代码。不要把它加进路由。

## Gotcha / 边界情况

- **JWT_SECRET 的默认值**：默认值是 `"dev-secret-do-not-use-in-production"`。云部署时如果忘记设置 `JWT_SECRET` 环境变量，应用正常启动并签发 token，但任何知道这个默认值的人都可以伪造合法 token。没有启动时的校验或警告。
- **INVITE_CODE 的默认值**：`"narranexus2026"`，同样没有强制要求在生产环境中覆盖。
- **token 有效期 7 天**：`JWT_EXPIRY_DAYS = 7`，没有 refresh token 机制。7 天后用户必须重新登录，前端会看到 401 并需要处理重定向到登录页。
- **`CurrentUser` 依赖在 local 模式下返回 None**：`get_current_user` 在 local 模式下返回 `None`，如果有路由用了 `Depends(get_current_user)` 并假设返回值非 None，local 模式下会 `AttributeError`。目前鉴权主要走中间件，这个函数几乎没被路由使用。

## 新人易踩的坑

修改 `AUTH_EXEMPT_PATHS` 或 `AUTH_EXEMPT_PREFIXES` 时，漏掉新的公开端点会导致云模式下这些路径突然开始要求登录，表现为前端请求 401，但本地开发时完全正常（本地模式跳过所有鉴权），因此这类 bug 在本地测试时根本发现不了。

`_is_cloud_mode()` 每次调用都重新读 `os.environ`，测试时如果没有设置环境变量，它永远返回 False，云模式代码路径在测试里默认不覆盖。要测试云模式逻辑，需要在测试里 monkeypatch `os.environ["DATABASE_URL"] = "mysql://..."` 或 `os.environ["DB_HOST"] = "some-host"`。
