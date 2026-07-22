---
code_file: backend/auth.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — 注释同步（行为不变）

`/api/transcription` bypass 前缀的解释注释随枚举改名更新
（free_tier_opted_out → free_tier_not_granted，见 transcription/service）。
review 二轮又扫掉 QUOTA_BYPASS_PREFIXES 头注释里"toggle the 'Use free
quota' switch off"和 `/api/quota` 行注"flip prefer_system_override"两处
死引用（开关/端点均已删）。纯注释，网关逻辑零变化。

## 2026-07-02 — QUOTA_BYPASS_PREFIXES 新增 /api/billing

NetMind 计费代理（[[billing]]）加入配额绕过前缀。超额用户正是最需要看"升级
Pro"面板的人——若不绕过，`provider_resolver` 会在路由执行前返回 402，把他们挡在
升级入口外。billing 调用本身无 NarraNexus LLM 成本（代理 NetMind）。与
`/api/providers` / `/api/quota` / `/api/transcription` 同属"无成本、需可达"类。
（与 `/api/notices` 在合并到 dev 时一并加入同一个前缀元组。）

## 2026-07-03 — /api/notices added to QUOTA_BYPASS_PREFIXES

Pure-metadata notices (list + mark-read); the primary notice class is
"provider broken/exhausted" — the very users the quota gate would 402.

## 2026-07-02 — SAFE_HTTP_METHODS 豁免（GH #61：额度耗尽锁死整个 dashboard）

上游 issue #61：免费额度耗尽后，`resolver.resolve_and_set()` 对每个非
`QUOTA_BYPASS_PREFIXES` 的 `/api/*` 路径都抛 `ProviderResolverError` →
402，**不分方法**。结果纯读接口（`GET /api/agents`、`GET /api/dashboard`
等）也被锁死——用户连自己已有的数据都看不到，而不仅仅是不能发起新的
LLM 调用。

**修法**：加 `SAFE_HTTP_METHODS = frozenset({"GET", "HEAD"})`，中间件在
`request.method in SAFE_HTTP_METHODS` 时也跳过 resolver（和
`QUOTA_BYPASS_PREFIXES` 走同一个 `return await call_next(request)` 分支）。
JWT 校验不受影响——安全豁免只加在 JWT 通过之后。

**为什么这样改是安全的**：`resolve_and_set` 的唯一副作用是把 LLM
provider 配置写进 `xyz_agent_context.agent_framework.api_config` 的
ContextVar（`set_user_config` / `set_provider_source`），不写
`request.state`，所以不存在"跳过它会让下游 GET handler 缺东西"的问题
——GET/HEAD handler 从不读这些 ContextVar，因为它们从不触发 agent
loop。改之前逐路由核实过：`backend/routes/**` 里每一个会花费 LLM 额度
的端点都是 POST（`/v1/chat/completions`、所有 agent-run/trigger 路由）；
唯一几个用 `StreamingResponse` 的 GET handler（如
`backend/routes/manyfold_files.py` 的文件读取端点）只是把磁盘文件流式
传出，不碰 LLM。因此这次豁免是方法级的、全路径生效，不需要给
`QUOTA_BYPASS_PREFIXES` 追加白名单条目。

**方法级而非路径级**：同一个 URL 下 GET 走豁免、POST 仍走 resolver（例
如 `GET /api/agents` 放行，`POST /api/agents` 仍 402）——测试
`test_post_on_same_path_still_gated` 专门验证这一点，防止未来有人把
`SAFE_HTTP_METHODS` 误改成按路径匹配。

## 2026-06-12 — AUTH_EXEMPT_PATHS 新增 /api/admin/migrate-identity

`/api/admin/migrate-identity`（`backend/routes/admin_migration.py`）加入豁免列表。该端点用 `X-Admin-Secret` header 自带凭证校验（`settings.admin_secret_key`），与 `/api/auth/netmind-login`（携带 NetMind loginToken）、`/api/invite/internal/issue`（携带 X-Internal-Secret）同属"自凭证、不走 JWT middleware"模式。离线批量迁移脚本没有 JWT，不豁免则 JWT middleware 会先返回 401，端点自身的 `_require_admin_secret` 检查永远不会执行。

## 2026-06-11 — _is_cloud_mode honors NARRANEXUS_DEPLOYMENT_MODE

Aligned `_is_cloud_mode()` precedence with the canonical utils.deployment_mode resolver the rest of the codebase uses: an explicit NARRANEXUS_DEPLOYMENT_MODE ("cloud"/"local") now wins; otherwise the unchanged legacy heuristic (DATABASE_URL non-sqlite -> cloud, else DB_HOST fallback, else local). dmg-safe — the desktop app doesn't set that env var so the safety heuristic still pins it local. Surfaced by Phase-1 testing: a sqlite + NARRANEXUS_DEPLOYMENT_MODE=cloud local smoke previously 404'd netmind-login because the old copy ignored the env var.

## 2026-06-11 — bcrypt password helpers removed; exempt list pruned

hash_password/verify_password (and the bcrypt import) deleted — cloud password login no longer exists, local login never had passwords. AUTH_EXEMPT_PATHS dropped /api/auth/register and /api/invite/internal/issue. users.password_hash column stays (no destructive DDL), it's just never read or written.

## 2026-06-11 — /api/auth/netmind-login added to AUTH_EXEMPT_PATHS

The NetMind-login endpoint carries its own credential (the NetMind loginToken, verified server-side inside the handler), so the middleware must let it through unauthenticated — same rationale as /login.

## 2026-05-18 — 杀掉 "first user" singleton fallback（彻底治本）

2026-05-13 的修复留了一个口子：local 模式 middleware 在 `X-User-Id` header 缺失时 fallback 到 `get_local_user_id()` 的"users 表第一行"，理由是"老前端 / bootstrap 兼容"。这个口子在多用户下又咬了一次：

**复现路径**：
1. 在本地装了 `binliang` 帐号（id=1）跑了一段时间，user_slots / user_providers 都配好
2. 用 `CreateUserDialog` 注册 `binliang3`（id=23），自动跳到 Settings 配 NetMind key + slots
3. 前端 `ProviderSettings.tsx` 的 `authFetch` 这条专用 fetch path **只发 JWT 不发 X-User-Id**
4. middleware 进 fallback → `request.state.user_id = "binliang"`
5. `_get_user_id` 路由 helper 优先信任 middleware（query 参数 `user_id=binliang3` 被忽略）
6. NetMind API key + 三个 slot 全部写到 `binliang` 名下
7. binliang3 跑 agent → resolver 查不到 binliang3 的 slot → `LLMConfigNotConfigured`
8. 用户视角："我明明配好了为什么不能用？"

**修法（彻底，铁律 #5 治本不治标）**：
- 移除 `get_local_user_id()`，改名 `ensure_local_default_user()` 只供 OS-side bootstrap 用，docstring 明禁 request-scoped 调用
- `auth_middleware` local 模式：无 X-User-Id 时**直接 401**（除 `AUTH_EXEMPT_PATHS` / `AUTH_EXEMPT_PREFIXES` 之外）。不再静默 fallback
- 前端 `ProviderSettings.tsx` 的 `authFetch` 改为同时发 JWT 和 X-User-Id（和 `api.ts` ApiClient 的 `getAuthHeaders` 对齐）
- `backend/routes/providers.py` 的 `_get_user_id` 移除 query 参数 `user_id` 这条 backup 通道——身份只能来自 middleware 设置的 `request.state.user_id`，URL 不再是 identity channel
- 所有 `/api/providers*` endpoint 删掉 `user_id: Optional[str] = Query(None)` 参数；前端相应去掉 `?user_id=...` 拼接

**为什么不留兼容层**：铁律 #2（不做向后兼容）。query 参数 user_id 这个 channel 本身就是 IDOR 漏洞——客户端可以拼 `?user_id=alice` 当 bob 登录时，把 alice 的数据写花。把这个 channel 彻底关掉比留 deprecation 路径更安全。

**已经落库的脏数据**：2026-05-18 03:57-03:58 之间 binliang3 的 NetMind key 落到 binliang 名下的两个 row（prov_d834ade2, prov_8f62e683） + 三个 slot row。debug branch 修完代码后用 SQL 删除，让 binliang3 重新走干净的 setup 流程。这次不写自动迁移——双用户场景下你不能确定哪条是"误写"，必须人工判断。

## 2026-05-15 — invite 路由改成 server-to-server

公开的 `/api/invite/request` 已废弃(架构 pivot:申请 UI + 发邮件移到
`narranexus-website`)。NarraNexus 现在只暴露 server-to-server 的
`POST /api/invite/internal/issue`,调用方是 website backend。

`AUTH_EXEMPT_PATHS` 相应:
- 移除 `/api/invite/request`
- 新增 `/api/invite/internal/issue`(它在路由 handler 内部用
  `X-Internal-Secret` header 校验,匹配 env `INTERNAL_INVITE_SECRET`——
  不走 JWT)

admin 侧 `/api/admin/invite/*` 仍需 staff JWT,不变。

## 2026-05-14 — 删除全局 INVITE_CODE 常量

`INVITE_CODE` 全局环境变量常量**已删除**。注册门禁改为 per-code 的 DB
机制(`invite_codes` 表 + `InviteCodeRepository`)。`routes/auth.py::register()`
不再 import / 比对它。

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
  - `backend/routes/auth.py` — 调用 `hash_password`, `verify_password`, `create_token`, `_is_cloud_mode`
  - `backend/routes/websocket.py` — 调用 `_is_cloud_mode`, `decode_token`（WebSocket 无法用 HTTP 头传 token，所以 WS 端自己验证）
  - `backend/routes/providers.py` — 通过 `request.state.user_id` 读取中间件注入的用户信息
- **依赖谁**：
  - `bcrypt` — 密码哈希
  - `PyJWT`（`jwt`）— token 生成和验证
  - 运行时读取 `DATABASE_URL`（或 fallback 到 `DB_HOST`）、`JWT_SECRET` 环境变量

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
- **token 有效期 7 天**：`JWT_EXPIRY_DAYS = 7`，没有 refresh token 机制。7 天后用户必须重新登录，前端会看到 401 并需要处理重定向到登录页。
- **`CurrentUser` 依赖在 local 模式下返回 None**：`get_current_user` 在 local 模式下返回 `None`，如果有路由用了 `Depends(get_current_user)` 并假设返回值非 None，local 模式下会 `AttributeError`。目前鉴权主要走中间件，这个函数几乎没被路由使用。

## 新人易踩的坑

修改 `AUTH_EXEMPT_PATHS` 或 `AUTH_EXEMPT_PREFIXES` 时，漏掉新的公开端点会导致云模式下这些路径突然开始要求登录，表现为前端请求 401，但本地开发时完全正常（本地模式跳过所有鉴权），因此这类 bug 在本地测试时根本发现不了。

`_is_cloud_mode()` 每次调用都重新读 `os.environ`，测试时如果没有设置环境变量，它永远返回 False，云模式代码路径在测试里默认不覆盖。要测试云模式逻辑，需要在测试里 monkeypatch `os.environ["DATABASE_URL"] = "mysql://..."` 或 `os.environ["DB_HOST"] = "some-host"`。
