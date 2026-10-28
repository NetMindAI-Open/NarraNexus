---
code_file: backend/main.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 - Drain before dependency shutdown

The lifespan owns detached request work through `app.state.run_tasks`, separately
from the run-ID lookup in `active_runs`. Its finally block waits for initialization,
execution and terminal persistence before stopping any housekeeping or workers or
closing the DB. Heartbeats and dependencies remain available throughout the drain.
Housekeeping cancellation is joined before DB close. Worker-stop errors do not
skip the remaining cleanup; errors are aggregated after all release attempts.
There is no run deadline;
shutdown-waiter cancellation is deferred through cleanup. This fixes graceful
backend SIGTERM losing a still-live run after its WebSocket handler exits.
External supervisor kill deadlines and early MCP shutdown remain outside this
backend-only guarantee; dependencies must remain alive until the backend exits.

## 2026-09-23 - Browser route ownership

The browser HTTP and WebSocket routes mount only after builtin registration
confirms a loaded builtin.browser module contribution. Disabling the plugin or
omitting it from a distribution removes both surfaces. They still mount before
plugin routers and the SPA fallback, preserving route precedence.

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

lifespan 里 `HEARTBEAT_INTERVAL_S` 改为从 `narranexus.platform.utils.run_liveness` 导入
（`sweep_stale_runs` 仍来自 `run_recorder`），行为不变。

## 2026-09-07 — 挂载 `GET /api/plugins/channels` 渠道目录

在 `channels_generic_router` 之后挂 `channels_catalog_router`，前缀 `/api/plugins`（**不是**
`/api/channels`：它是对插件注册表的读取，不是对某个 agent 绑定的操作，而 `/api/channels/*`
那一组全是 ownership 门控的）。它答的是「本部署有哪些渠道」，来源是 `ingress.channels`
注册表——目的是让前端不再自己维护一张六行渠道表，见
`backend/routes/channels/catalog.py` 的 mirror。

## 2026-09-03（批 2c）— 挂 `plugin_factory_router`，启动报告交给工场 service

## 2026-09-03（批 2b.5）— lifespan 接插件启动

`get_db_client()` 之后、`auto_migrate` 之前 `boot_backend_plugins()`；迁移完成后 `fire_startup()` +
`mark_healthy()`（到达「库已迁移、进程在服务」就是启动标记衡量的健康）。

## 2026-09-03（批 2a.4）— 挂载插件路由

`app.state.plugin_routes = mount_plugin_routes(app, KERNEL_REGISTRIES)` 放在壳路由之后、静态/SPA 之前
（顺序即安全边界，见 `plugins_host.py`）。

## 2026-08-28 — plugins_router 注册

新增 `from backend.routes.plugins.routes import router as plugins_router` 和
`app.include_router(plugins_router, tags=["Plugins"])`。router 自带 prefix
`/api/plugins`（`include_router` 不传 prefix），与 `admin_*` 系列同 pattern。
挂载 Settings → Plugins 面板需要的三个端点（list/install/uninstall），服务于
local/desktop 把 Claude Code / Codex CLI 从基础安装拆成按需插件的改造。见
[[backend/routes/plugins/routes.py]]。

## 2026-08-19 — admin_warn_router 注册

新增 `from backend.routes.admin.warn import router as admin_warn_router` 和
`app.include_router(admin_warn_router, tags=["AdminWarn"])`。router 自带 prefix
`/api/admin`，挂载敏感操作即时警告端点（`POST /api/admin/warn-user`，写一行
`user_notifications` + 一行 `ban_audit(action="warn")`）。与 `admin_suspend_router` /
`admin_gateway_key_misuse_router` 同 pattern；路径同步进 [[auth]] 的 `AUTH_EXEMPT_PATHS`。见
[[warn.py]]。

## 2026-08-19 — admin_gateway_key_misuse_router 注册

新增 `from backend.routes.admin.gateway_key_misuse import router as admin_gateway_key_misuse_router`
和 `app.include_router(admin_gateway_key_misuse_router, tags=["AdminGatewayKeyMisuse"])`。router
自带 prefix `/api/admin`，挂载网关 key 异常使用事件落库端点（`POST /api/admin/gateway-key-misuse`，
`gateway_key_misuse` 唯一写方）。与 `admin_suspend_router` 同 pattern。见 [[gateway_key_misuse.py]]。

## 2026-08-18 — `/health` 的结果缓存

`/health` **不是每请求一次 `SELECT 1`**：探测结果缓存 5 秒，**成功与失败同 TTL**。

这个端点公开无鉴权、nginx 直通，而每次探测占一条池连接、backend 的池是 10、没有任何
限流中间件。只缓存成功会把放大面正好留在数据库已经挂了的时刻，所以失败也缓存。

5s 这个值是对着容器 healthcheck 的 `interval: 30s` 选的：单个探测方按自己的节奏每次
仍是真往返，而**结果发布之后**到达的洪水收敛成一次查询（发布之前到达的不在此列，
见下面的已知限制）；两个探测方在 5s 内交叠时后到的吃缓存。**它与
`_HEALTH_DB_TIMEOUT_SEC` 的大小关系不是硬约束** —— 缓存 deadline 按**发布时刻**计算，
所以没有调用方会拿到一条「在生产它的过程中就已过期」的记录。

**发布守卫按探测的开始时刻排序，不是完成时刻。** 探测**可以**重叠（见下面的已知限制），
没有这条守卫的话，一个先开始、慢、且失败的探测会在返回时覆盖掉后开始但已成功的结论，
于是数据库恢复之后还会继续报 unhealthy 一个 TTL；反方向则会掩盖一次新故障。

**已知限制（有意接受）**：上面这套只在数据库健康时把放大面关到零——那时探测是毫秒级，
缓存几乎立刻生效。**数据库慢的时候，在飞探测发布之前到达的请求仍会各探各的。**

单飞（一把锁把这个窗口也盖住）能关掉它，本 PR 里试过又撤了：那套机器（per-loop 锁、
横跨排队与探测的共享预算、排队超时的降级路径）连续三轮评审各产出一个新问题，最后一版
自己制造了一条假的 unhealthy——预算被排队吃光后仍去探测，于是在**一次都没真正碰过
数据库**的情况下按完整 TTL 发布了 `"database": "timeout"`。一个为了让故障可见而加的
探针不该产出无依据的结论。这件事单独做，不跟着事故修复一起上。

**on-call 需要知道的一条**：数据库恢复后 `/health` 最多晚 5s 才翻绿，叠在
`retries: 5 × interval: 30s` 之上。把探针滞后误判成「数据库还没好」，代价是多等一轮。

## 2026-08-17 — `/health` 的 `database` 字段改成真探测

原来是硬编码字面量 `"database": "connected"` —— 一个**永远不可能为假的健康字段**，
承载的信息量为零。代价在 2026-08-17 兑现：后端每条查询都在报
`InterfaceError: (0, 'Not connected')` 的那 19 分钟里，容器 healthcheck 全程 healthy，
建立在它之上的 sentinel 容器状态探测因此一路绿灯，无人告警。

现在走**普通客户端路径**跑一次 `SELECT 1`（同一个连接池、同一套 task 级事务查找），
探的就是真实请求走的那条路——探测器不该有自己的特权通道，否则它证明的东西和线上无关。

**它是 `status` 的唯一例外。** 上面 2026-08-11 定的"只报告不判定"仍然成立（`failed`
不影响 `status`），但连不上数据库的后端无法服务任何鉴权请求，所以 DB 探测失败时
`status=unhealthy` 且返回 **503**——healthcheck 用的 `urlopen` 遇 5xx 抛异常，容器
因此转 unhealthy，这正是要的效果。

`_HEALTH_DB_TIMEOUT_SEC = 3.0`，压在 compose 里 healthcheck 自己的 `timeout: 5s`
之下：探测若比 healthcheck 活得久，docker 记录的是超时，我们那句「unhealthy + 原因」
就丢了。改这个常量前先看 `stacks/narranexus-app/compose.yml`（deploy 仓）。

**失败原因只回异常类名，原文只进日志。** 这个端点在 `backend/auth.py` 的公开白名单
里，线上是 `https://<app domain>/health`，无鉴权；而 pymysql 的错误串带 RDS 端点
（`Can't connect to MySQL server on '<endpoint>'`）和库用户名（`Access denied for
user 'x'@'<内网IP>'`）。故障期恰恰是它最可能被扫到的时候。类名仍能区分「连不上 /
认证失败 / 池子死了」，这正是 on-call 需要的粒度。**日志那行必须写在 `except` 块
内**——`logger.exception` 在离开 except 后调用没有活跃异常可渲染，只会打出字面量
`NoneType: None`，真正的原因哪里都不剩，比改造前的可观测性更差。

**部署耦合（改 503 之前必读）。** healthcheck 用 `urllib.request.urlopen`，5xx 会抛
异常，所以 503 会让容器转 unhealthy——这正是让容器状态监控**能看见**数据库故障的
机制（2026-08-17 它看不见）。但 compose 里有 **4 个**服务声明
`depends_on: backend: condition: service_healthy`：`frontend`，以及经
`x-python-common` 锚点继承的 `mcp` / `workers` / `model-sync`。

后果要完整说，因为漏掉的那一半最容易踩：在数据库不可用期间执行 `docker compose up`
**不是部分降级，而是整条命令失败**（`dependency failed to start: container
narranexus-backend is unhealthy`）。什么都起不来——前端起不来（ops 侧 Caddy 失去上游，
公网入口返 **502**；前端自己没有 host 端口映射，Caddy 反代到 `narranexus-frontend:80`），
**workers 也起不来**，那是全部 channel trigger、ModulePoller、Job trigger 和 message bus。

于是恢复顺序被硬性约束成「先修数据库，再部署」。冷启动本来就需要数据库（lifespan 建池），所以变的是「故障期重新部署」而
非首次启动；已经在跑的栈会继续服务，直到探针在 `retries: 5 x interval: 30s` 后翻转。

这是**有意接受**的代价：另一条路（healthcheck 改指浅探针 `/healthz`）会把本次要关掉
的盲区原样装回去。不要用「DB 失败仍返回 200、只把 status 置 unhealthy」来绕开耦合——
一个永远不可能为假的健康字段不承载信息，原 bug 就是这么活下来的。

## 2026-08-13 — admin_suspend_router 注册

新增 `from backend.routes.admin.suspend import router as admin_suspend_router` 和
`app.include_router(admin_suspend_router, tags=["AdminSuspend"])`。router 自带
prefix `/api/admin`（`include_router` 不再传 prefix），挂载账户停用机制的三条端点
（`POST /api/admin/suspend`、`POST /api/admin/reinstate`、`GET
/api/admin/account-state/{user_id}`）。与 `admin_migration_router` /
`admin_quota_router` 同 pattern。见 [[suspend.py]]。

## 2026-08-11 — cloud 关 API docs + JWT 启动 fail-fast（安全审计 P2-1/P0-2）

两处安全接线：(1) `FastAPI(...)` 在 cloud 模式传 `docs_url=None, redoc_url=None,
openapi_url=None`（`_docs_kwargs` 按 `_is_cloud_mode()` 组装、import 期求值）——不再
把完整 API schema/Swagger 暴露给攻击者；开发者在 local/dev 仍有 docs。(2) lifespan 启动
早段（建库前）调 `assert_jwt_secret_safe()`——cloud 模式 `JWT_SECRET` 未设/为默认值即
`raise` 拒绝启动。**上线次序**：上 prod 前须先确认 prod `.env` 的 JWT_SECRET 是真值，
否则守卫会自伤把服务拦在启动外。

## 2026-08-10 — product analytics ingestion router

Registers authenticated `/api/analytics/events`. Startup schema migration runs
before traffic, so capture sites can safely write the additive fact table.

## 2026-08-10 — external telemetry removed

The backend no longer registers a vendor analytics sink and shutdown no longer
flushes a network queue. Product events go only through the first-party
database writer. This makes local/desktop privacy independent of environment
variables or release-build secrets.

## 2026-08-07 — 挂载 runs_router(/api/runs)

run 级控制面,目前只有 owner 的停止请求。见 [[runs]]。

## 2026-08-03 — lifespan 预热价目表（把 1.5s 同步 import 挪出 event loop）

[[model_pricing]] 的表是懒加载的，这本身对多数进程是对的。但它的**首次触发点在
`await record_cost(...)` 内部** —— `import litellm` 约 1.5s 的同步开销就落在 event loop 上，
顿住当时所有并发 WS 帧。

lifespan 里 `asyncio.to_thread(model_pricing.warm_cache)`，fire-and-forget + done callback
（不留裸 `create_task`）。**必须是线程不是任务**：代价是同步 import，放进 task 里照样阻塞
event loop。失败只是退回懒加载路径（旧行为），所以只 warning 不抛。

## 2026-07-31 — 无条件 reconcile → 心跳判活清扫（启动 + 周期）

原「backend 启动把所有 state='running' 行翻 failed」在 trigger run 也被
记录后是误杀器（trigger run 活在别的容器，backend 重启不代表它死了）。
换成 [[run_recorder]] 的 `sweep_stale_runs`：只翻心跳停跳 ≥3 拍的行，
启动跑一次 + 每 60s（HEARTBEAT_INTERVAL_S*2）周期跑 —— 任何进程里
孤儿化的 run 都会在 ~90s+一个周期内落账，不再依赖「恰好 backend 重启」
这个偶然时机。周期 task 带 done-callback（lesson #2），shutdown 时
cancel（stale_run_sweep_task）。代价：backend 自己带走的 run 最多晚
~90s 才翻 failed —— 读侧 run_is_live 已经把它当死的，UI 无感。

## 2026-07-28 — manyfold sync router + config-change webhook middleware

`ENABLE_MANYFOLD_API=1` 块内新增 `manyfold_sync_router`（`GET /manyfold/jobs`、
`GET /manyfold/channels`）和 `config_change_webhook_middleware` 注册。middleware
故意放在该块**最后**注册：Starlette LIFO 下它成为最外层，观察得到最终 status
code；它只在 response 侧行动（job/channel/provider 路由的 2xx 写请求后
fire-and-forget webhook），对 OPTIONS/非 2xx 完全透明，所以不触碰 auth/CORS 的
LIFO 陷阱。无 `MANYFOLD_SYNC_WEBHOOK_*` env 时是纯透传。

门控之外零影响：`ENABLE_MANYFOLD_API` 未设时（EC2 prod/dev、本地、DMG 的现状）
router 和 middleware 都不注册，中间件栈长度不变。见
`[[routes/manyfold/sync.py]]`。

## 2026-07-28 — lifespan 里的配额子系统整块移除

不再构造 `SystemProviderService` / `QuotaService` / `QuotaRepository`，
`app.state` 上的 `system_provider` 和 `quota_service` 一并消失（路由改为按需
构造 wallet client）。`gateway_spend_reconciler` 后台任务的启停也删除 ——
计量已经在网关里实时完成，事后对账没有对象了。

`ProviderResolver` 的构造从三个依赖降到一个。

## 2026-07-27 — 免费额度 gateway spend reconciler 接入 lifespan

`lifespan` 启动时紧接 executor-reaper 调 `maybe_start_gateway_spend_reconciler()`
(存 `app.state.gateway_spend_reconciler_task`),teardown `cancel()`。云端+网关
(`SYSTEM_DEFAULT_LLM_GATEWAY_URL`)才真起,本地/桌面 no-op。周期把免费档 agent
运行的真实 token 用量(网关 SpendLogs)补记入配额——代理的非 Anthropic 模型 CLI
报 0 token,不补记免费额度就永远不扣。绝不 force-stop(铁律 #14)。见
`[[../src/narranexus/platform/services/gateway_spend_reconciler.py]]`。

## 2026-07-22 — review 修复:seed/reconcile 移出启动关键路径

两个 marketplace seed 从 `yield` 前的 `await` 改为 `create_task`(team seed 走网络最坏拖数分钟会冻启动、超 healthcheck);首次 reconcile 也移除(run_forever 首轮即做)。加 shutdown cancel。

## 2026-07-22 — skill seed 接入 lifespan

继 team seed 之后,registry host 再跑 `seed_skill_marketplace`(发布 repo 的
marketplace_skills/ first-party 技能,含 NetMind vision/audio default)。best-
effort、非阻塞;两个 seed 共用一个 try 块。

## 2026-07-21 — /api/marketplace/teams 挂载 + team seed(Team Marketplace)

挂 marketplace_teams_router 于 /api/marketplace/teams。lifespan 在
registry host(cloud / SKILL_MARKETPLACE_LOCAL_REGISTRY)跑 seed_team_marketplace
(best-effort,非阻塞):从 narra.nexus 拉 9 个官方模板 → 本地 template store
→ catalog。desktop 客户端不 seed(proxy 云端)。

## 2026-07-21 — Skill Marketplace 路由挂载 + 对账器 lifespan 任务(stage 5/6)

挂载 `marketplace_skills_router` 于 `/api/marketplace/skills`(marketplace
命名空间按对象拆分;`/api/marketplace/teams/*` 预留给 bundle 分享)。lifespan
新增 SkillSyncService:启动先跑一遍 reconcile_all,再起 run_forever 循环任务
(`SKILL_SYNC_INTERVAL_SECONDS`,默认 1800,0 关闭),shutdown cancel;
done-callback 记录非预期退出(fire-and-forget 教训 #2)。

## 2026-07-10 — feedback_router 注册

`backend.routes.feedback` 挂在 `/api`（完整路径 POST /api/feedback）。web UI
反馈弹窗的服务端中继——不让浏览器直连团队接收端（CORS/杀开关/身份可信）。

## 2026-07-02 — billing_router 注册

新增 `from backend.routes.billing import router as billing_router` 和
`app.include_router(billing_router, prefix="/api/billing", tags=["Billing"])`。
NetMind 订阅/计费代理（[[billing]]）——`/plans`、`/subscription`。Phase 1。

## 2026-07-03 — notices router mounted at /api/notices

See backend/routes/notices.py.md.

## 2026-06-22 — narramessenger_router 注册

新增 `from backend.routes.channels.narramessenger import router as narramessenger_router`
和 `app.include_router(...)`，挂载路径 `/api/narramessenger`（`bind` / `unbind` /
`credential`）。与 lark/slack/telegram channel 的 router 同 pattern。

## 2026-06-17 — executor idle-cull reaper 接入 lifespan

`lifespan` 启动时调 `maybe_start_executor_reaper()`(存 `app.state.executor_reaper_task`),
teardown 时 `cancel()`。云端+broker 才真正起,本地/桌面 no-op(返回 None)。
回收空闲 per-user executor 容器,只碰空闲的(铁律 #14)。见
`[[../src/narranexus/platform/agent_runtime/executor_reaper.py]]`。

## 2026-06-12 — admin_migration_router 注册

新增 `from backend.routes.admin.migration import router as admin_migration_router` 和对应的 `app.include_router(admin_migration_router, tags=["AdminMigration"])`。router 自带 prefix `/api/admin`，最终挂载路径为 `POST /api/admin/migrate-identity`。与 `admin_quota_router` 同 pattern（自带 prefix，`include_router` 不再传 prefix 参数）。

## 2026-06-11 — invite routers unwired

invite_router / admin_invite_router imports and include_router calls removed alongside the route modules' deletion (invite-code mechanism retired).

## 2026-06-09 — versioned migration runner wired into lifespan

The startup lifespan now calls `run_pending_migrations(db)` after `auto_migrate`
and the one-shot self-heals — the universal hook for the versioned data-migration
ledger (migrations/ [[__init__]]). Wrapped defensively (best-effort): a migration
error is logged and never blocks startup. This is what carries the
unified-memory backfill to EVERY environment (cloud / run.sh / DMG) without a
deploy-side step.

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

`agents_artifacts_router` (from `backend.routes.agents.artifacts`) is now
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
`setup_logging`. See `src/narranexus/platform/utils/logging/` for the
public surface.

A new HTTP middleware is registered alongside `auth_middleware`:
`backend.middleware.access_log.access_log_middleware`. Order matters
— FastAPI runs middleware LIFO, so we register `auth_middleware`
FIRST and `access_log_middleware` SECOND, which means access_log
wraps auth and 401 / 402 responses still produce one access line.

A new admin-only router is mounted at `/api/admin/logs` (see
`backend.routes.admin.logs`). It surfaces the on-disk
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
  - `narranexus.platform.utils.db.db_factory` — `get_db_client` / `close_db_client` 管理连接池生命周期
  - `narranexus.platform.utils.db.schema_registry.auto_migrate` — 启动时执行表结构迁移
  - 壳层路由模块：`websocket`, `agents`, `auth`, `providers`, `inbox`, `channels` 等。
    `jobs` / `skills` / home-assistant / 六个 IM 渠道路由**不在这里**——它们是各自 builtin 的
    `backend.routes` 贡献（批 3c.5），由 `mount_plugin_routes` 挂载；照旧清单去 import 会找不到模块。

## 设计决策

**中间件注册顺序（LIFO）**

FastAPI/Starlette 的中间件以 LIFO 执行：**最后注册的最先处理请求（最外层）**。
`main.py:499-503` 的注册顺序是 `auth` → `body_size` → `access_log` → `CORSMiddleware`，
所以实际链路是 **CORS → access_log → body_size → auth → routes**，
**CORS 在最外层、跑在 auth 之前**（本段旧文案写反过，说 auth 先于 CORS——不成立）。

即便如此，`auth_middleware` 里那句 `if request.method == "OPTIONS": return await call_next(request)`
仍然是承重的：`CORSMiddleware` 只对**带 `Origin` 头**的 preflight 自行短路应答，
其余 OPTIONS 会继续往内传，落到 auth 上；没有这句放行，它们会被 401。
任何新增的、对所有请求生效的 HTTP 中间件都要同样放行 OPTIONS。
顺序契约由 `tests/backend/test_body_size_gate.py` 断言钉住（详见下方 2026-08-20 小节），
不再只靠注释。

**lifespan 而非 startup/shutdown 事件**

旧版 FastAPI 用 `@app.on_event("startup")` / `@app.on_event("shutdown")`，新版推荐 `asynccontextmanager` 的 `lifespan` 参数。这里选择新版做法，好处是数据库连接的初始化和清理代码放在同一个函数里，语义更清晰，也不会忘记配对。

**前端静态文件的条件挂载**

如果 `frontend/dist/index.html` 存在，就挂载 `/assets` 静态目录并添加 SPA fallback 路由；否则只暴露一个 `GET /` 健康检查。这让同一套代码既能作为纯 API 服务（开发时前端跑在 Vite 单独进程），也能在生产/dmg 模式下直接服务打包后的前端。SPA fallback 是 catch-all `/{full_path:path}`，必须在所有 API 路由之后注册，否则会劫持 `/api/*` 路径。

**schema auto_migrate**

启动时调用 `auto_migrate(db._backend)` 自动执行建表/加列。这个函数对 SQLite 和 MySQL 都能工作，但它直接访问了 `AsyncDatabaseClient` 的 `_backend` 私有属性，算是一个轻微的封装泄露。如果将来 db_factory 的内部结构调整，这里需要同步更新。

## Gotcha / 边界情况

- **OPTIONS 请求必须在 auth 中手动放行**：见上文「中间件注册顺序（LIFO）」。任何新增的 HTTP 中间件，如果需要对所有请求生效，都必须同样放行 OPTIONS，否则跨域调用全部失败，症状是浏览器报 CORS error 但服务器日志里看到的是 401。
- **SPA fallback 的路由顺序**：前端挂载代码在 `main.py` 底部，必须在所有 `app.include_router(...)` 之后执行。如果新增路由但忘记在前端挂载代码之前注册，SPA fallback 会先匹配到新路径并返回 `index.html`，导致 API 调用失效。
- **`auto_migrate` 访问私有属性**：`db._backend` 是私有字段，重构 `AsyncDatabaseClient` 时需要检查这里。

## 新人易踩的坑

CORS 已经在最外层了；删掉 `auth_middleware` 里的 OPTIONS 放行逻辑仍会让不带 `Origin` 的 preflight 被 401。中间件顺序和这段放行必须一起考虑。

在 `lifespan` 里 yield 之后报错（比如 `close_db_client` 抛出异常），uvicorn 会打印错误但不会阻止进程退出，这是正常的关闭行为，不是 bug。

## 2026-07-13 — office-watch 路由

注册了 office 实时预览的两个 router:`office_watch_router`(挂 `/api`,authed:`/office-watch/open`)和 `office_watch_public_router`(挂 `/api/public`,token 鉴权:`/office-watch-proxy/{token}/{port}/{path}`)。见 `backend/routes/office_watch/proxy.py.md`。

## 2026-07-30 — Agent Migration 路由

注册了 `migrate_router`(挂 `/api/migrate`,tags=["Migration"]):`/detect` `/scan` `/apply`,导入其他框架的 agent(Claude Code/Codex/OpenClaw/Hermes)进 NarraNexus。**local/desktop only** —— 三个端点都经 `_require_local_or_raise()` 在 cloud 返回 503(cloud 无用户文件系统)。见 `backend/routes/migrate.py.md`。

## 2026-08-11 — 挂载 `TeamSummaryWorker`

与 memory worker 同一份契约：机会性、逐 team 隔离、失败保留旧总结、任何东西都不等它（铁律 #14）。

**关停顺序有讲究**：在 `close_db_client()` **之前** stop。它的轮询循环持有那个 client，
一次落在拆解中途的 pass 会在每一次干净关机时打出一条令人困惑的连接错误。有测试钉住这个次序。

## 2026-08-11 — `/health` 暴露总结 worker 的上一轮

`last_pass` 记了却没人读，等于没记：它要关的那个盲区（「全都很安静」和「全都在失败」
看起来都像 worker 好好活着）在计数不出进程的情况下原样存在。

**只报告，不判定**：单个团队的 provider key 坏掉不该让容器探针失败，所以 `status` 不依赖 `failed`。

## 2026-08-20 — 第三层 HTTP 中间件 body_size(#334 r3 I1/r4 C2)

实际链路(注册 LIFO):**CORS → access_log → body_size → auth → routes**。
存在理由:FastAPI 在 solve_dependencies 之前就读完 body,路由依赖做不成
「快拒」——中间件是唯一先于框架碰流的层(详见 [[body_size.py]],它只管
自己的按路由上限,本文件只记「栈里有几层、谁包谁」)。与既有约束的
关系:CORS 仍必须最外层(它的 413 也要带 ACAO);**body_size 必须在
access_log 内、routes 外**——外一层它的 413 从访问日志消失,内到
routes 就退回假门。OPTIONS 复核结论:preflight 无 Content-Length,
天然放行。顺序由 tests/backend/test_body_size_gate.py 断言钉住——
含「CORS 在四层最外」(历史上唯一真被破过的那条,#334 r5 I1 补钉;
断言是相对序而非 `names[0]`,将来在最外再加 tracing 层是合法改动)——
注释顺序契约本仓已破过一次,不再只靠注释。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

Teams is no longer wired here: the router and the summary worker come from the `builtin.teams` manifest. Import time calls `register_builtins_for_import()` before `mount_plugin_routes()` so the route table is complete for the approval snapshot; lifespan starts/stops every `backend.workers` contribution with `host="backend"` through `start_backend_workers`/`stop_backend_workers`.

## 2026-09-04 · plugin-owned router (batch 3c.5)

jobs / skills / home-assistant / the six IM channel routers are no longer imported or included here — they are `backend.routes` contributions of their builtins (batch 3c.5) mounted by `mount_plugin_routes`. main.py keeps only shell-level routers.

## 2026-09-04 · generic channel credentials (batch 4b)

Mounts the generic channel router at `/api/channels` (shell-level: any channel in ingress.channels).

Batch 6b.3: the team marketplace router and seed are no longer wired here — the router is a `backend.routes` contribution of builtin.teams and the seed runs on the `onDidStartBackend` host event emitted at the end of `_seed_marketplaces` (registry-host check via `is_registry_host()`).

Batch 6 fix: the lifespan hands its db client to `plugins_boot.set_host_db`; `mount_user_plugin_routes` runs at import after the builtin routers.

## 2026-09-07 — SPA fallback answers 404 for `v1/ manyfold/ api/ ws/`

`spa_fallback` returns a JSON 404 (not `index.html` + 200) for any unregistered path under those four prefixes. Two reasons: a distribution that leaves a builtin out must make that builtin's old routes 404 (a platform readiness probe getting 200 + HTML would call a missing service healthy), and a frontend request to a wrong endpoint must never receive the HTML shell as data. Every other path still serves the SPA shell (`tests/backend/test_spa_fallback_404.py` runs both directions with a real `frontend/dist`).

## 2026-09-07 — /health no longer reports team_summary

The block read app.state.team_summary_worker, which nothing sets since the worker moved to builtin.teams' backend.workers contribution (workers process); its heartbeat is the '[team.summary] pass:' log line there. A block that reads state nothing sets is not a signal, and the test that kept it green faked the attribute.
