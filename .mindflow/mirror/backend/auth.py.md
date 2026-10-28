---
code_file: backend/auth.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — service bearer 不再绕过封号门

`nx-agent` 服务 bearer 分支原来在 `_account_state` 之前就 return（注释写明是"已知边界"）。
事故里攻击者把沙箱 token 拿到公网重放；封号后 token 在 72h TTL 内照常可用。现在该分支
用同一个 `_account_state` 读取器与 `NON_TRANSACTING_USER_STATUSES` 判定，封号即 403
`account_suspended`（`auth_error_response` 带 path/method/user_id，与 JWT 分支同形）。
provider/quota resolver 仍跳过（数据面调用不是 LLM 花费）。

后果与代价：封号用户**已在跑**的后台工作（job、经 executor 回调 backend 的 agent turn）
在下一次调 backend 时就拿到 403 而停下，不再等 gateway key 吊销。旧注释担心的
「热路径上每请求读 users 表」不成立——`_account_state` 有 TTL 缓存，每用户每 TTL 至多一次读库。

## 2026-09-03（批 2a.4）— `PLUGIN_EXEMPT_PREFIXES`

插件路由声明 `auth="none"` 时由 `plugins_host` 在启动期填入的豁免前缀集合（运行期可变，所以是 set 而不是
常量元组）。两处判断（JWT 门与本地模式 X-User-Id 门）都并入 `_is_plugin_exempt`，其余路径仍 fail-closed。

## 2026-09-03 — `_is_cloud_mode()` 转发到 `narranexus.kernel.deployment.is_cloud_mode`

函数与 docstring 里的 DMG 安全理由保留，实现改为一行转发；优先级（显式 env > DATABASE_URL >
DB_HOST > local）与本文件原实现完全相同，只是不再是第二份副本。

## 2026-08-19 — AUTH_EXEMPT_PATHS 新增 `/api/admin/warn-user`

敏感操作即时警告端点（[[warn.py]]，写 `user_notifications` + `ban_audit(action="warn")`）
进精确路径豁免。它在 handler 内用 `X-Admin-Secret` 自凭证（与 [[suspend.py]] /
[[gateway_key_misuse.py]] 同一把锁），调用方是私有 monitor 转发软信号检测的机器请求、没有
用户 JWT，用用户认证 gate 反而不对（否则 middleware 先 401、端点不可达）。与
`/api/admin/gateway-key-misuse` 同模式。

## 2026-08-19 — AUTH_EXEMPT_PATHS 新增 `/api/admin/gateway-key-misuse`

网关 key 异常使用事件落库端点（[[gateway_key_misuse.py]]，`gateway_key_misuse` 唯一写方）进
精确路径豁免。它在 handler 内用 `X-Admin-Secret` 自凭证（与 [[suspend.py]] 同一把锁），
调用方是内部 server-to-server 路径转发权威事件的机器请求、没有用户 JWT，用用户认证 gate
反而不对。与 `/api/admin/suspend` / `/api/admin/runtime/status` 同模式：豁免同时跳过 quota
resolver，正确——它根本不是用户面请求。

## 2026-08-13 — 账户状态闸门（account-state gate）+ 30s TTL 缓存 + 停用端点豁免

middleware 在 JWT 验签通过、`request.state.user_id` 已就位之后、provider/quota
resolver 之前，多加一道**账户状态闸门**：读该用户的 `users.status`，若落在共享的
`NON_TRANSACTING_USER_STATUSES = {banned, blocked, deleted}`（从
`narranexus.platform.schema` 顶层 import，[[entity_schema.py]] 的单一真相源，取代
原来本文件里那份本地 `_NON_TRANSACTING_STATES` 字面量），立即返回 **403**
（`ACCOUNT_SUSPENDED` code，见 [[auth_errors]]）。这样一个「仍然有效」的 JWT，
在其命名的账户被停用后就不能再交易——账户停用机制（[[suspend.py]]）把
`users.status` 置为 `banned`，`blocked`/`deleted` 是既有终态，同样不可交易。

**读走 UserRepository（BINARY 大小写敏感）**：`_account_state` 经
`UserRepository.get_user`（`WHERE BINARY user_id ... LIMIT 1`）读，而不是
`db.get_one`。停用**写**侧用的就是 `WHERE BINARY`；若读侧用 MySQL 默认的
大小写**不敏感** collation，一个仅大小写不同的 look-alike user_id 就能绕过闸门。
只在真 MySQL 上出错——SQLite 无此 collation，`database.py` 还会把 `BINARY`
关键字剥掉，所以单测**抓不到**这个 case。Repository 是**惰性 import**（函数内），
避免 auth.py 在进程启动期把 DB 层拉进来。`get_user` 的强 `_row_to_entity` 转换
在 status 值不在 `UserStatus` 时会抛——本函数的 `except Exception` fail-open 顺带
兜住（返回 "active"、不缓存）。

**为什么是 403 而不是 401**：401 会被 SPA 读成「会话死亡」而跳登录，但重登只会
再铸一枚同样有效的 token 又被弹回，成死循环。403（已认证、不被许可）配一个
专属 code，前端据此展示「账户不可用」而非登录循环。`ACCOUNT_SUSPENDED` 刻意
**不在** `SESSION_DEAD_CODES` 里。

**30s TTL 进程内缓存 + 10k 条上界**：middleware 每个已认证请求都跑，逐请求查一次
users 表会把它压上热路径。`_account_state_cache`（module 级 dict，per-process、
best-effort、自愈）缓存 `(status, expiry)` ~30s，常见情形（活跃账户狂发请求）零 DB。
`invalidate_account_state(user_id)` 在 suspend/reinstate 后由 [[suspend.py]] 调用，
让改动在**本进程**立即可见；跨进程 staleness 由 TTL 兜底（一个刚被停用的账户
最多再交易约 30s）。写入前若 `len(cache) >= _ACCOUNT_STATE_MAX_ENTRIES (10_000)`
则**整体 `clear()`**（不逐条淘汰）——缓存本就 best-effort、会自动重填，整体清空是
最省的上界，最多让仍活跃的用户多走一次 DB 读，防止一串各异的 user_id（大量短会话，
或拿垃圾但合法的 JWT 打闸门）把 dict 无界撑大。`import time` 已上移到模块顶部。

**Fail OPEN**：`_account_state` 在任何查库异常、或 user 行查不到时，返回
`"active"` 且**不缓存**。一次瞬时 DB 抖动绝不能把全体用户锁在产品外——这道闸
是为拦住某个具体被停用账户，不是要变成全局可用性依赖。查不到行也不缓存，避免
懒创建的用户行出现后一条 stale miss 长期滞留。

**豁免名单**：三条 admin 停用路径进豁免（它们各自用 `X-Admin-Secret` 自凭证，
被停用账户也没有可用 JWT，用用户认证 gate 反而循环）——`/api/admin/suspend` 与
`/api/admin/reinstate` 是精确路径进 `AUTH_EXEMPT_PATHS`；`GET
/api/admin/account-state/{user_id}` 是路径参数路由，无法精确匹配，改进
`AUTH_EXEMPT_PREFIXES` 的 `/api/admin/account-state/`。

**已知边界 · 仅 nx-service bearer 不受闸门约束**：账户闸门放在用户 JWT 分支上，
`_is_nx_service_bearer` 分支在其之前 `return`，所以**服务身份**（executor→mcp→backend
的 nx-agent bearer）不过这道闸。这个边界**只**适用于 nx-service bearer——即被停用
用户**在途的后台工作**（已在跑的 job/agent 经服务 bearer 调 backend）：它不由 HTTP
闸门掐断，而是由运营侧**吊销其网关 key**（LLM 花费随即失败）、以及处置到位时
**销毁其 executor** 来止住。在内部热路径上再加一次 users 表读不划算，故在途止血
刻意下放给 key-revoke。

**用户自己的 WS token 现在会被 gate**（更正旧说法）：曾经账户闸门只在 HTTP
middleware，而 middleware 豁免 `/ws/*`，于是一个被停用用户拿自己有效的 JWT 仍能经
WebSocket **发起新 run**（WS 是产品**主**路径）。现已在 WS handler 内补上同一道闸门
（见 [[websocket.py]]），复用**同一个** `NON_TRANSACTING_USER_STATUSES` 与**同一个**
`_account_state` 读取器（TTL 缓存、fail-open），两面不会漂移。`_account_state` 因此被
WS 端 import——它现在是账户闸门的公共入口。

## 2026-08-11 — AUTH_EXEMPT_PATHS 新增 NarraMessenger prewarm 双端点

`/api/narramessenger/prewarm` + `/api/narramessenger/prewarm/status`(F28 语音
快答):caller 是 NarraMessenger 后端(机器对机器,没有用户 JWT),handler 内
用 per-agent `bearer_token` 自凭证(`hmac.compare_digest` 常量时间比较)——与
`/api/admin/runtime/status` 同模式。豁免同时跳过 quota resolver,正确:预热
是纯基础设施操作,不花 LLM 额度。

## 2026-08-10 — product analytics bypasses the quota resolver

`/api/analytics` still requires normal user authentication but skips provider
resolution: event ingestion is metadata-only and spends no LLM quota. This is
essential at the paywall, where exhausted users must still be able to record
subscribe and checkout actions instead of receiving a resolver 402 first.

## 2026-08-10 — nx-agent 服务身份旁路（蓝图 Q6，MCP caller auth）

云 JWT 段在 `decode_token` 之前多一条判定:bearer 是 `nx-agent:` 位置记录
(mcp 容器 HttpStore 原样转发的 executor→mcp 身份通道)→ 验证算法走共享的
[[identity/verify]](本文件只保留降级策略与日志;bearer 解析走 module 公开面
`parse_bearer_identity`/`BEARER_AGENT_PREFIX`,不伸私有名)→ 公钥
(`NX_IDENTITY_PUBLIC_KEY_FILE`,[[identity/tokens]])验 bearer 的 identity_token
段(BEARER_FIELDS 末位)Ed25519 token,
sub 即生效 user(`request.state.nx_service_authed=True`),路由侧照旧对目标
agent_id 做 owner 校验。与 manyfold gateway-token 平级的服务信任先例,但有
两点刻意不同:
1. **绝不 fail-open**——nx-agent bearer 打到 backend 只可能是服务调用,无 token
   /无公钥/验签失败/bearer 声明的 user 与 sub 不符,一律 401(每种 reason 单独
   落 WARNING,0806 的可诊断纪律);mcp 侧中间件 fail-open 是因为它是数据面,
   这里不是。
2. **早退跳过 provider/quota resolver**——数据面服务调用是 repository 操作不是
   LLM 花销,欠费用户的 awareness 更新不该被 402(与 SAFE_HTTP_METHODS 同理)。
测试:`test_auth_nx_service_bearer.py`。

## 2026-08-06 — 每个 401 带上 `code` + 一行 `[auth-reject]` 日志

middleware 的三个 401 出口（无 Bearer / 过期 / 非法）和 local 模式缺
`X-User-Id` 的那个出口，改用 [[auth_errors]] 的 `auth_error_response()`：
响应体从 `{"detail": ...}` 变成 `{"detail": ..., "code": ...}`，并且**每次
拒绝都写一行 WARNING**（token 类还带这枚 token 自己的 iat/exp）。

为什么非改不可：这几处原本是裸 `_json_response`，**一行日志都没有**。
2026-08-02 线下活动那小时的 10 个带 token 401，事后只能看到路径和状态码，
"到底是自然过期还是签名密钥换了"至今无解——而这两者在日志里的区别就是
`exp` 是否还在未来。`_json_response` 现在只剩 402 配额那一处在用。

`resolve_current_user_id`（86 处路由调用的共享身份出口）改抛
`AuthError(IDENTITY_UNRESOLVED)`：middleware 已经放行、处理器却拿不到身份，
那是我们自己的接线 bug，**不是**会话死亡，不该把用户踢下线。分类表见
[[auth_errors]]。

## 2026-07-22 — 共享 CSRF 守卫 `reject_cross_origin`

新增 `reject_cross_origin(request)`:tokenless 的 local-mode 写(marketplace
skills/teams publish)共用的 CSRF 防线。规则——无 Origin(CLI/同源)放行;
Origin 为 loopback(localhost/127.0.0.1)放行;其余含 `Origin: null`(沙箱
iframe / data: 表单)一律按跨站 403;`Sec-Fetch-Site: cross-site` 兜底纵深。
两个路由模块原本各自(或跨模块 import)私有实现,现下沉到 auth 单一来源。

## 2026-07-21 — marketplace 公开读扩展到 teams/*

`_is_marketplace_public_read` 的前缀从单一 skills/* 改为 (skills/*, teams/*)
元组:desktop 客户端浏览/下载 team 模板同样无 cloud JWT,GET 读面匿名放行。
POST(install-preflight/publish)仍走严格认证。


## 2026-07-21 — Marketplace 可选认证读面 + publish 自凭证豁免(stage 8 试跑发现)

真实缺口:桌面端用户不登录 cloud,原全局中间件把 marketplace 读端点一并
401,桌面根本拉不到目录。修复:
- `MARKETPLACE_PUBLIC_READ_PREFIX`(仅 **GET** `/api/marketplace/skills/*`)
  为「可选认证」——带 JWT/X-User-Id 则照常解析身份(cloud UI 的 installed
  标注继续工作),不带则匿名放行,路由侧用新 helper
  `resolve_optional_user_id`(缺省返 None、不抛)优雅降级。POST(install)
  不在此列,维持严格认证。
- `/api/marketplace/skills/publish` 加入 AUTH_EXEMPT_PATHS:自带
  X-Publish-Token 凭证(MARKETPLACE_PUBLISH_TOKEN),与 migrate-identity
  同模式——CI/ops 发布者没有用户 JWT。


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
provider 配置写进 `narranexus.platform.agent_framework.api_config` 的
ContextVar（`set_user_config` / `set_provider_source`），不写
`request.state`，所以不存在"跳过它会让下游 GET handler 缺东西"的问题
——GET/HEAD handler 从不读这些 ContextVar，因为它们从不触发 agent
loop。改之前逐路由核实过：`backend/routes/**` 里每一个会花费 LLM 额度
的端点都是 POST（`/v1/chat/completions`、所有 agent-run/trigger 路由）；
唯一几个用 `StreamingResponse` 的 GET handler（如
`backend/routes/manyfold/files.py` 的文件读取端点）只是把磁盘文件流式
传出，不碰 LLM。因此这次豁免是方法级的、全路径生效，不需要给
`QUOTA_BYPASS_PREFIXES` 追加白名单条目。

**方法级而非路径级**：同一个 URL 下 GET 走豁免、POST 仍走 resolver（例
如 `GET /api/agents` 放行，`POST /api/agents` 仍 402）——测试
`test_post_on_same_path_still_gated` 专门验证这一点，防止未来有人把
`SAFE_HTTP_METHODS` 误改成按路径匹配。

## 2026-06-12 — AUTH_EXEMPT_PATHS 新增 /api/admin/migrate-identity

`/api/admin/migrate-identity`（`backend/routes/admin/migration.py`）加入豁免列表。该端点用 `X-Admin-Secret` header 自带凭证校验（`settings.admin_secret_key`），与 `/api/auth/netmind-login`（携带 NetMind loginToken）、`/api/invite/internal/issue`（携带 X-Internal-Secret）同属"自凭证、不走 JWT middleware"模式。离线批量迁移脚本没有 JWT，不豁免则 JWT middleware 会先返回 401，端点自身的 `_require_admin_secret` 检查永远不会执行。

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
3. ~~Catches `QuotaExceededError` and emits HTTP 402 with
   `error_code: QUOTA_EXCEEDED_NO_USER_PROVIDER`.~~ **Gone since the
   2026-07-28 wallet migration.** The pre-run quota gate was the thing
   that raised it; once the free tier became an ordinary provider card,
   exhaustion moved to a mid-run gateway refusal and the exception, the
   402 and its `error_code` all ceased to exist — `git grep
   QUOTA_EXCEEDED_NO_USER_PROVIDER` now matches only prose. The frontend
   half (an `api.ts` interceptor dispatching a banner) survived with no
   emitter until 2026-07-30, when it was deleted too; the funnel it used
   to open now lives in [[MessageBubble]]'s in-chat buttons.

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

- **JWT_SECRET 的默认值 + 启动校验**（2026-08-11 加固）：默认值 `"dev-secret-do-not-use-in-production"` 供 local 模式用。**cloud 模式启动时 `assert_jwt_secret_safe()` 会拒绝默认值/空值**——`main.py` lifespan 在建库前调用它，`JWT_SECRET` 未设或等于默认值即 `raise RuntimeError` 拒绝启动（与 `routes/artifacts/_token.py` 的 secret 同一 fail-fast 姿态）。所以云上"忘设 JWT_SECRET 用已知默认值签发可伪造 token"这条已堵：宁可不启动也不用已知密钥签名。local 模式仍用默认值（单可信用户、无需 JWT）。
- **token 有效期 7 天**：`JWT_EXPIRY_DAYS = 7`，没有 refresh token 机制。7 天后用户必须重新登录，前端会看到 401 并需要处理重定向到登录页。
- **`CurrentUser` 依赖在 local 模式下返回 None**：`get_current_user` 在 local 模式下返回 `None`，如果有路由用了 `Depends(get_current_user)` 并假设返回值非 None，local 模式下会 `AttributeError`。目前鉴权主要走中间件，这个函数几乎没被路由使用。

## 新人易踩的坑

修改 `AUTH_EXEMPT_PATHS` 或 `AUTH_EXEMPT_PREFIXES` 时，漏掉新的公开端点会导致云模式下这些路径突然开始要求登录，表现为前端请求 401，但本地开发时完全正常（本地模式跳过所有鉴权），因此这类 bug 在本地测试时根本发现不了。

`_is_cloud_mode()` 每次调用都重新读 `os.environ`，测试时如果没有设置环境变量，它永远返回 False，云模式代码路径在测试里默认不覆盖。要测试云模式逻辑，需要在测试里 monkeypatch `os.environ["DATABASE_URL"] = "mysql://..."` 或 `os.environ["DB_HOST"] = "some-host"`。

## 2026-07-30 — bypass 名单 +runtime/status

`/api/admin/runtime/status` 进 AUTH_BYPASS（handler 内 X-Admin-Secret 自凭据，
读-only）——同 migrate-identity 模式，服务 deploy 仓 alert watcher。

## 2026-08-05 — AUTH_EXEMPT_PATHS 新增 /api/auth/funnel-report

客户端 auth 漏斗故障上报：上报者按定义刚登录失败、没有 session。路由自带
stage 白名单+限流+log-only 防护（见 routes/auth.py mirror）。

## 2026-09-04 · webhook transport (batch 4c)

`_is_channel_webhook_path` — the exact `/api/channels/{channel}/webhook/{agent_id}` shape is auth-exempt (the handler verifies the binding's secret); every other `/api/channels/*` route keeps the normal auth.

Batch 6c.2: identity comes from the bound authProviders plugin (`backend.auth_provider.auth_provider()`). Local branch: the provider names the user (builtin.auth.local reads `X-User-Id`); cloud branch: after the nx service-bearer path the provider decodes the bearer and its `AuthError` code/status are reported verbatim (a `None` identity is `token_invalid`); `get_current_user` uses the same provider. `decode_token`/`create_token` stay here for the NetMind provider and the login route.

## 2026-09-07 — segment-boundary plugin exemptions; plugin quota bypass; provider-first authentication

path_under_prefix() matches a plugin's auth-exempt / quota-bypass prefix on a path-SEGMENT boundary (Starlette routes by segment; string prefixes let /api/x/acme.w exempt /api/x/acme.w2 and /webhook exempt /webhook-admin). PLUGIN_QUOTA_BYPASS_PREFIXES honours RouterSpec.quota_bypass (declared in the contract, previously ignored by everyone). Authentication now asks the bound kernel.auth provider FIRST: a distribution binding a cookie/SSO/header provider is reachable without a bearer (the 'Bearer or 401' gate ran before the seam and made it impossible to exercise); a None identity with no bearer is TOKEN_MISSING (after the anonymous marketplace read), with a bearer TOKEN_INVALID. The nx service bearer keeps its earlier, separate trust path.


## 2026-09-07 — 每一个前缀**集合**都走 `path_under_prefix`（改一处扫一类）

`path_under_prefix` 是本文件已有的段边界判定（`path == base or path.startswith(base + "/")`），
它当初是为了修「`/api/x/acme.w` 豁免了 `/api/x/acme.w2/...`」而引入的。但那一轮只把它接到了
**插件**那两个集合上；三个静态集合仍然是裸 `startswith`，而它们的条目**都没有尾斜杠**：

- `QUOTA_BYPASS_PREFIXES`（`/api/providers`、`/api/quota`、`/api/admin`、`/api/auth`、
  `/api/transcription`、`/api/billing`、`/api/analytics`、`/api/notices`）
- `AUTH_EXEMPT_PREFIXES`（两处调用点：cloud 分支与 local 分支）
- `MARKETPLACE_PUBLIC_READ_PREFIXES`（GET-only）

也就是说 `/api/quota-admin`、`/api/authz`、`/api/billing-internal` 这类**兄弟路径**会连带
拿到豁免/旁路。今天对着 250 条 golden 路由验证过 0 命中——是潜伏，不是活 bug——但代价是
「下一条加进列表的前缀会不会顺手开掉它的兄弟」这件事没有任何机制回答。四处全部改用
`path_under_prefix`。

未改的三处 `startswith` 是**有意**的，它们不是前缀集合：`_is_manyfold_path` 的
`/v1/`、`/manyfold/` 和中间件里的 `/api/`、`/ws/` 都自带尾斜杠，本身就是段边界；
其余命中是 `Bearer ` 与 `mf_` 前缀，与路径无关。

测试：`tests/backend/test_path_under_prefix.py`（helper 本身的参数化单测——精确相等 / 真
后代 / 同串前缀兄弟 / 带尾斜杠的前缀 / 前缀长于路径，外加一条读源码的守卫，断言这三个集合
不再和 `startswith` 出现在同一行），以及 `tests/backend/test_auth_middleware_quota.py` 里
`/api/quota-admin/grant` 仍然 402 的桩路由。
