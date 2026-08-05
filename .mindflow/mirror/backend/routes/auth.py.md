---
code_file: backend/routes/auth.py
last_verified: 2026-08-05
stub: false
---

## 2026-07-31 — active_run 富集只认 chat/manyfold 来源

`/api/auth/agents` 的 running 行 SELECT 加 `` `trigger` IN
('chat','manyfold') ``：trigger run（lark/team/job）现在也是
state='running'（run 可观察性），不滤的话 ChatPanel 的 auto-reconnect
会把网页聊天接到一条 Lark turn 上。chat/manyfold 恰好是 BackgroundRun
的两个面 —— 本富集从来描述的就是它们；其余 run 走 WS 观察端点
（tail-follow）看，不进聊天页。依赖 step 0 的诚实 trigger 标签
（[[step_0_initialize]]）。

## 2026-07-29 — 删除 agent_cli_sessions 的级联清理(T7)

删 agent 时的 5b 步(`DELETE FROM agent_cli_sessions WHERE agent_id = %s`)移除。

它当初补上的理由值得记住(2026-07-28):那张表加进来时**漏了级联**,于是句柄比
agent 和它的工作区都活得久,一个被回收的 `agent_id` 会继承一个指向已死 transcript
的句柄,而且行只增不减没人清理。

现在表本身已摘掉注册([[schema_registry]]),这段 `DELETE` **留着反而会报错**。
根因也不复存在:没有任何按 agent_id 存的持久物——transcript 每轮写、每轮删。

## 2026-07-28 — delete_agent 级联补 agent_cli_sessions

`agent_cli_sessions`（resume 句柄表，见 [[schema_registry.py]]）加表时漏了级联，
`delete_agent` 的 leaf-first 扫除里没有它。按既有形状补在 `instance_jobs` 之后
（同为 agent_id 直接键的运行期表）：显式 DELETE + 计数进 `deleted_counts`，
零行时不入 stats（沿用本函数惯例）。

漏扫的后果有两层：句柄行**比 agent 和它的 workspace 活得更久**，agent_id 若被回收
就会继承一个指向已删 transcript 的句柄；而且没有任何别的机制会清它们，只会无限
累积。测试：tests/backend/test_delete_agent_queue_cascade.py（含"别的 agent 的句柄
必须存活"与"零行不进 stats"两条）。

## 2026-07-28 — 两个 provisioner 串行，免费额度必须先落地

新用户登录后 agent 必挂 "Claude API error: unknown"。真因是并发：登录路径
上原本并排 fire-and-forget 了两个 provisioner，它们各自读"这用户是否已有可
用配置"来决定要不要绑 slot，而两边都在对方写入之前读到了空——典型 TOCTOU。
NetMind 那条要先铸 key 所以后完成，于是把 slot 改绑到了用户自己那张**刚开
户、零余额**的 Power 卡上；$10 钱包躺着没人用，每次调用都报上游错误。

修法是串行：`_provision_providers` 先 await 免费额度，再 await NetMind。
免费额度排第一不是随手定的顺序——它是我们**确定有余额**的凭据；新开的
Power 账户没有额度，而已充值的老账户不受影响（NetMind provisioner 见到完整
配置时本来就只注册不抢绑，串行之后它才真的能见到）。

拆成 `_provision_providers`(协程) + `_schedule_provider_provisioning`(调度)
两层，是为了让测试能直接 await 协程观察顺序：调度是 fire-and-forget，而
TestClient 一返回响应就关掉 event loop，让出过一次的 task 根本跑不完。
**测试里两个 fake 都必须 await**——真 provisioner 的 HTTP/DB 挂起点正是竞态
的成因，不 await 的 fake 在 `asyncio.gather` 下也会串行执行，对着坏版本一样
绿（已用 gather 回插验证过）。

免费额度的开通也不再只在 `is_new` 里跑，改成每次登录都跑：provisioner 靠
key alias `free::{user_id}` 自带幂等，无条件重跑能让首次撞上钱包服务故障的
用户在下次登录自愈，而不是永久卡死。

## 2026-07-28 — 首登不再播种 token 配额，改为开钱包

首次 NetMind 登录时的 `quota_service.init_for_user` 换成
`schedule_ensure_free_tier_provider`：开一个 $10 钱包并把它注册成一张普通
provider 卡。

因此登录响应里的 `has_system_quota` / `initial_*_tokens` 三个字段删除 ——
开通是 fire-and-forget 的（登录绝不能被钱包服务拖住或拖垮），响应时刻它
还没跑完，报任何值都是猜的。调度调用本身也包了 try/except：这条路径上任何
异常都不许冒泡成登录失败。

## 2026-07-23 — delete_agent cascades memory_consolidation_queue

New step 7c: `DELETE FROM memory_consolidation_queue WHERE agent_id = ?`.
Without it the consolidation worker idle-triggered the dead agent's scopes
on every poll and spammed "no owner row" warnings forever (see
[[memory_consolidation_worker.py]] 2026-07-23 for the worker-side
self-heal). Test: `tests/backend/test_delete_agent_queue_cascade.py`.

## 2026-07-21 — create_agent 默认技能装机(stage 9)

创建 agent 成功后 fire-and-forget 一个 asyncio task 调
`SkillMarketplaceService.install_defaults`(带 done-callback,教训 #2)。
非阻塞、非致命:registry 离线/未部署时优雅跳过;builtin 物化机制不受影响,
仍是离线兜底。


## 2026-07-17 — `/api/auth/agents` first-paint sort by recent conversation

`get_agents` now sorts the returned `AgentInfo` list by activity
before responding: two stable passes (agent_id asc, then activity
desc) put the most-recently-active conversation on top. Activity =
`max(last_assistant_at, created_at)`; both are `format_for_api`
fixed-width ISO-UTC strings ("...Z"), so lexical string compare is
correct — no datetime parsing needed. This is the pre-hydration
BASELINE only: the frontend re-sorts with the same rule PLUS fresh
local session activity (see [[agentGroupUtils]] `sortAgentsByActivity`),
so an agent still jumps the instant the user talks to it. The SQL
`ORDER BY agent_create_time DESC` is retained purely as deterministic
input order for the enrichment queries; the Python sort decides the
final order.

## 2026-07-13 — netmind-login 门禁改挂 power 轴（本地双模式登录）

`netmind_login` 的可达性从 `_is_cloud_mode()` 改成 `is_power_login_enabled()`
（[[deployment_mode]]），于是本地部署开启 `NARRANEXUS_ENABLE_POWER_LOGIN` 后
也能用 NetMind(Power)账号登录,与纯本地用户名登录并存,用户自选。到达 handler
后 line ~235 的 `schedule_ensure_netmind_provider`（见 [[netmind_provisioner]]）
照旧触发,本地也会自动铸 Power provider + slot。**注意:`login()`（用户名登录）
与 `create_user()` 仍挂 `_is_cloud_mode()`**——它们表达"云端禁用用户名/建号"这一
安全语义,本地双模式必须保留,不能改成 power 轴。

## 2026-07-10 — NetMind login auto-registers the user's provider

`netmind_login`, right after issuing the app JWT + `_schedule_login_rearm`, now
fire-and-forgets `schedule_ensure_netmind_provider(user_id, netmind_token)` (see
[[netmind_provisioner]]). Cloud login IS NetMind login, so the user's NetMind
provider is minted+registered automatically — no manual "use this account"
button. Non-fatal by construction: login never blocks on or fails from NetMind
minting; the provisioner self-guards on the feature flag and only activates slots
when the user has no active config (register-always, activate-if-fresh).

## 2026-07-09 — agent-delete cascades agent_slots

The delete-agent cascade (step 14f, before deleting the agent row) now
``DELETE FROM agent_slots WHERE agent_id = %s`` so a removed agent leaves no
orphan per-agent LLM overrides.

## 2026-07-07 — `trigger` is a MySQL reserved word: must be backticked

The `last_assistant_preview` window query filters on the `trigger` column.
`trigger` is a **MySQL reserved word**; written bare it raises `(1064, ...
near 'trigger IS NULL ...')` on prod (MySQL) — 2585 WARNINGs in 2 days,
sidebar previews silently empty. SQLite tolerates a bare `trigger`, so local
dev never caught it. Fix: `` `trigger` `` (backticks work on both dialects).
Any raw SQL touching this column must quote it — see the same fix in
[[_dashboard_helpers]]. Regression: `tests/backend/test_trigger_reserved_word_sql.py`
emulates MySQL's rejection in-process (SQLite can't reproduce it).

## 2026-06-23 — sidebar preview excludes group-chat replies (forward only)

`/api/auth/agents`' `last_assistant_preview` window query filters
`trigger != 'message_bus'`. New team group-chat runs are tagged at creation
([[step_0_initialize]] / [[models]]), so their replies are excluded → previews
stay clean **going forward**.

**Why historical rows can't be filtered** (investigated 2026-06-24): the root
leak is that a message-bus run records its reply under the agent's *regular*
narratives (default `*_default_N-*` AND topic `nar_*`), identical to a 1:1
reply — same `trigger_source` (the user), no marker. Most replies never reached
`bus_messages` (e.g. rabbit: 2 rows vs many leaked events), so content-matching
is incomplete; and the same reply is duplicated across default + topic
narratives, so neither narrative-id nor actors separate them. Pre-tag previews
therefore can't be cleaned by query — they age out as the agent has genuine 1:1
activity, or the user clears history.

Real fix (pending): stop bus runs writing into 1:1 narratives — route them to a
dedicated team-room narrative in narrative selection.

## 2026-06-11 — identity hardening: create_agent / timezone / onboarding

The last three routes that trusted a client-supplied user id now derive identity from auth_middleware via `resolve_current_user_id`: POST /agents (body created_by removed — clients could create agents under anyone's account), POST /timezone and GET+POST /onboarding (body/query user_id removed). Old clients sending the extra field are harmless (pydantic ignores unknown fields); old clients omitting X-User-Id/JWT get 401. scripts/spikes/bench_narrative_models.py updated to send X-User-Id.

## 2026-06-11 — legacy cloud auth removed (invite codes retired)

/login is local-only now (cloud -> 404, points at netmind-login); /register deleted outright; /create-user gained a cloud 404 guard (it was an unauthenticated open account-creation endpoint sitting in AUTH_EXEMPT_PATHS — known hole, now closed). Invite-code mechanism retired entirely per 2026-06-10 owner decision (signup == first NetMind login, everyone gets the free-tier quota): routes/invite.py and routes/admin_invite.py deleted, InviteCodeRepository and invite_code_gen deleted, INVITE_AUTO_ISSUE_CAP / INTERNAL_INVITE_SECRET config gone. The invite_codes TABLE survives — it holds the old-user-id -> email mapping the legacy-user migration script needs.

## 2026-06-11 — POST /api/auth/netmind-login (Phase 1 user-system unification)

New cloud-only login endpoint: verifies a NetMind loginToken via `NetmindAuthClient` (one network call to NetMind's /user/balance), lazily upserts the local user (`UserRepository.upsert_netmind_user`, user_id = NetMind userSystemCode), seeds the free-tier quota on FIRST login (registration no longer exists — first login is registration; invite codes are gone per 2026-06-10 decision), then issues NarraNexus's own JWT. Error mapping: bad token -> 401, NetMind unreachable/contract drift -> 502 (never disguised as a credential failure). `_get_netmind_auth_client()` is module-level for test monkeypatching. The legacy /login (cloud password branch) and /register are slated for removal in the same feature branch.


## 2026-06-10 — run-liveness helper moved to background_run.py (shared)

The `_parse_db_utc` / `_run_is_live` heartbeat-freshness rule (running
events row trusted only while `last_event_at` is within 3 missed beats)
moved to `background_run.py` as `parse_db_utc` / `run_is_live`, because
the WS reconnect path now needs the SAME answer to "is this run actually
alive?" (see websocket.py 2026-06-10 entry — zombie running rows must be
reported as `run_ended`, not reconnect-looped). auth.py keeps a local
`_run_is_live = run_is_live` alias; behavior of the agents-list
active_run filter is unchanged.

## 2026-06-08 — account deletion clears memory_* by agent_id

Account deletion dropped `instance_social_entities` from `instance_sub_tables` and added a loop deleting every `memory_<kind>` table by agent_id (using `MEMORY_KINDS`), so a deleted account leaves no orphan rows in the unified memory store.

## 2026-06-10 — analytics endpoints: identity from middleware only (review fix)

PR #24 review hardening. All three analytics endpoints (`GET/PUT
/settings/analytics`, `POST /funnel`) now derive the user exclusively from
`request.state.user_id` via the shared `_require_request_user()` helper
(401 when absent). `SetAnalyticsOptOutRequest` lost its `user_id` field and
`FunnelEventRequest` lost `properties`:

- Opt-out previously trusted a client-supplied `user_id` (query/body), so
  any authenticated user could read or flip another user's privacy
  preference. Now impossible by shape — the request can't name a target.
- The funnel endpoint previously forwarded an arbitrary client `properties`
  dict to PostHog, letting a client override the server-derived `surface`
  (dict.setdefault doesn't protect present keys) or inject junk. The
  setup_* events carry no payload by design, so client properties are no
  longer accepted at all.

Frontend `api.ts` methods changed in the same commit (no user_id param, no
properties param). Tests: `test_user_settings_routes.py` (per-user
isolation + 401), `test_funnel_capture.py` (client properties ignored).

## 2026-06-09 — funnel redesign: /api/auth/funnel endpoint (setup_* events)

Added `POST /api/auth/funnel` for the three pure-UI setup events
(`setup_entered`, `setup_skipped`, `setup_completed`). These events have no
backend signal, so the frontend reports them through this endpoint.

Key design decisions:
- **Identity from middleware only** (`request.state.user_id`, set by
  `auth_middleware`). The body never carries identity — prevents a user from
  spoofing events onto another user's funnel.
- **Whitelist only** — `_ALLOWED_FUNNEL_EVENTS` (a `frozenset`) accepts only
  the three `setup_*` constants. Any other event name returns 400. This
  prevents the endpoint from becoming a generic event firehose.
- **Delegates to `track()`** — inherits opt-out, distinct_id hashing, and the
  surface label exactly like every other funnel event. Never raises.
- `FunnelEventRequest` is a small inline `BaseModel` with `event: str` and
  `properties: dict | None`.

`create_agent` no longer emits any analytics (`EVENT_AGENT_CREATED` is
removed). The funnel no longer tracks agent creation.

## 2026-06-08 — analytics opt-out endpoints

Added `GET /api/auth/settings/analytics` and `PUT /api/auth/settings/analytics`
for the frontend privacy toggle. Both delegate to `UserSettingsRepository`
(new dependency added this task). The GET returns `{"opted_out": bool}` where
the absence of a user_settings row means `false` (opted in by default). The
PUT accepts `{"user_id", "opted_out"}` and upserts the row.

`SetAnalyticsOptOutRequest` is a small Pydantic `BaseModel` defined inline
(not in `schema/` — it has two fields and no reuse elsewhere). `BaseModel` and
`UserSettingsRepository` are imported at the top of the file alongside the
existing imports.

Tests: `tests/backend/test_user_settings_routes.py`.

## 2026-06-08 — funnel: signed_up event

`create_user` calls `identify_user` + `track(EVENT_SIGNED_UP)` on the
success path. Additive instrumentation — best-effort, never raises.

The `identify_user` traits deliberately carry only `role` — NOT
`display_name`. The analytics layer hashes the distinct_id, so shipping the
raw display name as a person trait would re-leak exactly the identity the
hash is meant to hide. Keep identity-bearing fields out of traits.

`create_agent` carries no analytics instrumentation. `EVENT_AGENT_CREATED`
was removed in the 2026-06-09 funnel redesign; create_agent is not a
tracked funnel milestone.

## 2026-05-21 — onboarding checklist endpoints

Added `GET /api/auth/onboarding` + `POST /api/auth/onboarding` for the
new-user onboarding checklist card (cloud version). State lives inside
`users.metadata` under the `onboarding_progress` key — no new table.

Design points:
- **Write-once-true**: `POST` only applies fields explicitly `True`; None
  and False are ignored, so a completed step can never be reverted. This
  is deliberate — the checklist must not oscillate when a user creates
  their first agent then deletes it.
- **Merge, don't clobber**: `users.metadata` is a shared JSON blob, so the
  handler reads the full dict, updates only the `onboarding_progress`
  sub-key, and writes the whole dict back (`_read_onboarding` helper +
  `_ONBOARDING_METADATA_KEY` constant).
- `provider_configured` is **not** stored — the frontend derives it live
  from provider count (that step is gated by SetupPage before the card
  shows). Only `first_agent_created` / `template_applied` / `dismissed`
  are persisted.

Sits next to `/api/auth/timezone` — both are JWT-gated user-scoped
settings endpoints. Tests: `tests/backend/test_onboarding.py`.

## 2026-05-19 — `/api/auth/agents` 附加最近一条 assistant 回复（NM sidebar preview）

每个 `AgentInfo` 现在带 `last_assistant_preview` + `last_assistant_at` 两个字段，供前端左边栏第二行显示"这个 agent 最近说了什么"。

实现走窗口函数：`ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY created_at DESC)`，单条 SQL 一次性拿到列表里每个 agent 的最近一条非空 `events.final_output`。已有的 `idx_events_agent_created` 索引直接 cover 这个查询，不需要新加索引。过滤 `final_output IS NOT NULL AND final_output != ''` 把崩在中途的 run 和空回复都排掉。

server 端把 `final_output` 拍平空白后截到 200 chars（前端再切到 60，多出来的 200 给前端将来调宽度留余量）。失败仅 warn-log，不阻塞 list 返回——和 active_run 一样定位为增强字段。

## 2026-05-14 — register() 改用 DB 邀请码（替换全局 INVITE_CODE）

`register()` 不再比对 `backend.auth.INVITE_CODE` 全局环境变量（该常量已
删除）。新流程走 `InviteCodeRepository`：

1. `get_by_code` 快速预检——码存在且 `status=='issued'`，否则返回明确错误
   （已用 / 失效 / 无效）。纯为 UX，不是真正的 gate。
2. 校验密码、用户名、user 不存在（顺序不变）。
3. `consume(code, user_id)` —— 单条带条件 UPDATE（`WHERE status='issued'`），
   原子消费 issued→used。并发抢同一码只有一方 affected==1。
4. insert user；失败则 `revert_consume` 把码退回 issued，不白烧。

注册不再"全局开关"——有没有可用的码由 `invite_codes` 表决定。Mode B 的
发码 / cap / waitlist 全在 `backend/routes/invite.py` + `admin_invite.py`。
设计记录为作者本地 worklog，不入库；关键决策已写在上文。

## 2026-05-13 — `/api/auth/agents` 返回 active_run 字段（Phase C）

GET 端点为每个 agent 附带 `active_run: ActiveRunInfo | null`——前端
据此显示 Agent 卡片上的"Running"徽章（复用 Jobs status badge 的视觉
pattern）。

实现：在 agents 主 SELECT 之后再做一次 SELECT 把所有 `agent_id IN
(...)` 且 `state='running'` 的 events 行一次性查出来（IN-列表合并避
免 N+1），按 agent_id 索引到 dict，再 zip 进 AgentInfo。失败仅
warn-log，不阻塞 list 返回——active_run 是增强而非核心。

新加的 `ActiveRunInfo` Pydantic 模型在 `schema/api_schema.py`，导出在
`schema/__init__.py`。Spec: `2026-05-13-agent-runtime-lifecycle-and-stream-resilience-design.md` §4.1.8

## 2026-04-16 addition — quota seeding on register

Successful `/api/auth/register` in cloud mode now calls
`app.state.quota_service.init_for_user(user_id)` after the user row is
inserted. The call is defensive:
- QuotaService disabled (local / feature off) → returns None, response
  still succeeds with `has_system_quota: false`
- DB failure during quota insert → logged, registration still succeeds
  so the user doesn't lose their account over a quota-subsystem bug

The response shape gained `has_system_quota`, `initial_input_tokens`,
and `initial_output_tokens` fields. The frontend RegisterPage uses them
to render a one-shot welcome toast on successful cloud-mode registration
— skipped silently in local mode where the flag is false.

# routes/auth.py — 用户认证与 Agent CRUD 路由

## 为什么存在

这个文件承担了两个职责：用户认证（登录、注册）和 Agent 的完整生命周期管理（创建、更新、删除、列表）。Agent CRUD 放在 auth 路由下而不是 agents 路由下，是因为这些操作需要用户身份验证（"这个 agent 属于谁"），在概念上更接近用户管理而非 agent 资源操作。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(auth_router, prefix="/api/auth")`；前端登录页、Agent 管理页
- **依赖谁**：
  - `AgentRepository` — Agent 的基础 CRUD
  - `UserRepository` — 用户的增删查、last_login 更新、timezone 更新
  - `InviteCodeRepository` — 注册时校验 + 原子消费邀请码
  - `backend.auth` — `hash_password`、`verify_password`、`create_token`、`_is_cloud_mode`
  - `xyz_agent_context.bootstrap.template.BOOTSTRAP_MD_TEMPLATE` — 创建 Agent 时写入工作区的初始化文件
  - `xyz_agent_context.settings.settings.base_working_path` — Agent 工作区根目录

## 设计决策

**登录接口的双模式**

登录接口在 local 模式下只需要 `user_id`（不校验密码），在 cloud 模式下需要 `user_id + password`，返回 JWT token。同一个接口，根据 `_is_cloud_mode()` 的返回值走完全不同的逻辑路径。这让前端可以调用同一个接口，通过响应里是否有 `token` 字段来判断当前模式。

**注册只在 cloud 模式可用**

`register` 接口在 local 模式下直接返回错误。Local 模式下用户只能通过 `create-user`（管理员操作）创建账号。Cloud 模式下用户通过 invite code 自助注册。

**Agent 删除的级联顺序**

`delete_agent` 按"从叶到根"的顺序删除：先删动态 Memory 表（按实例/Narrative ID）→ 删 Jobs → 删 Instance-Narrative Links → 删各种实例子表 → 删 Module Instances → 删 Events → 删 Narratives → 删 MCP URLs → 删 agent_messages → 删工作区目录 → 最后删 Agent 本身。这个顺序是为了避免外键约束失败，同时确保没有孤立数据残留。

动态 Memory 表（`json_format_event_memory_*` 和 `instance_json_format_memory_*`）需要运行时发现，因为它们的表名包含模块类型后缀，不是固定的。代码里对 SQLite 和 MySQL 分别用不同的系统表查询语法来发现这些表。

**Bootstrap.md 触发首次配置**

创建 Agent 时会在工作区写入 `Bootstrap.md`，Agent 在首次运行时检测到这个文件并执行初始化流程。`bootstrap_active` 字段在 GET agents 接口里通过检查文件是否存在来计算，是文件系统状态而非数据库字段。

## Gotcha / 边界情况

- **Agent 列表使用原始 SQL**：`get_agents` 直接构造 SQL 查询（`WHERE created_by = %s OR is_public = 1`），而不是通过 `AgentRepository`。这打破了 Repository 模式的封装，但允许更灵活的可见性规则（自己的 + 公开的）。
- **`password_hash` 的遗留用户处理**：登录时如果 `user` 对象上没有 `password_hash` 属性，会再次查原始 DB 行。这是为了兼容通过 `create-user` 创建的无密码用户（local 模式遗留）。
- **工作区目录和 agent 是 1:1 绑定的**：目录名是 `{agent_id}_{user_id}`，删除 agent 时会删掉整个目录（包括所有上传的文件）。这个操作不可逆。

## 新人易踩的坑

`delete_agent` 里的 `stats` 字典只记录被实际删除的行数（`cnt > 0` 才写入），如果某个表里没有这个 agent 的数据，该表不会出现在删除统计里。不要用 `stats` 的 key 来判断"是否执行了删除操作"，正确的理解是"哪些表删除了至少一行"。

## 2026-08-05 — 注册/登录漏斗观测（[signup-funnel]/[login-funnel]）+ /funnel-report

Base #11 recvre9LlfwXAP + #12 recvre9LlfyyxT 的观测修复,三层：

1. **signup 路由**：限流命中（send-code / signup）与 policy reject 现在都落
   `[signup-funnel]` 日志;上游 refusal 的细节日志在 register client 源头
   （见其 mirror）,路由层不重复。
2. **netmind-login**：NetmindAuthError → 401 之前落 `[login-funnel]` warning,
   携带 client 层带出的上游 status+msg（永不带 token）。
3. **POST /auth/funnel-report（新,pre-auth）**：浏览器直连 NetMind 的登录步骤
   失败时,服务端本来一无所知——前端 fire-and-forget 报到这里,落
   `[login-funnel] client stage=... email=... detail=...`。
   - **与既有 POST /auth/funnel 的区别**：那个是登录后的 setup_* 产品埋点
     （走 analytics）;这个是**登录不成的人**的故障上报（走日志）。
   - 防滥用：stage 白名单（3 个）、detail ≤300 且去换行（防日志伪造）、
     per-email 限流 10/min——超限**静默 200**（诊断通道绝不在用户已经在看的
     失败上再叠一个错误）、local mode 404、无 DB 写入、无可探测响应体。
   - 必须同时进 backend/auth.py 的 AUTH_EXEMPT_PATHS（上报者按定义没有
     session）,有测试钉住。

测试：tests/backend/test_auth_funnel_observability.py（13 条）。

### 2026-08-05 R2（review 修正）：限流按「被消耗的资源」选 key + 丢弃可见化

R1 的 per-email 限流 key 选错了资源维度（review 指出）：/funnel-report 消耗的
是**全局日志**,不是 per-email 的什么东西——email 是调用方自己填的,换 email
即换桶,对攻击者形同虚设;OAuth 失败 email 恒空,全世界共享一个 anon 桶,
真故障时通道自己变成无声采样器。修正：
- 三层桶全过才记：global 120/min（XFF 轮换也打不穿的兜底）+ per-IP 30/min
  （XFF 首跳,Caddy 前置;不在代理后时可伪造——所以才有 global 层）+
  per-email(或 IP) 10/min。
- 丢弃对用户仍静默 200,对运维不再静默：每分钟一条
  `[login-funnel] dropped N client report(s)` 汇总。
- `signup_ui_error` stage 删除（无调用方,铁律 #2）。
- 未做（有意）：/funnel-report 与 1600+ 行处的 /funnel 物理相邻——纯搬家
  diff 噪音大于收益,mirror 里两者区别已写死。

### 2026-08-05 R3（review 修正）：桶顺序即不变量 + XFF 从右数

R2 两个残留（review 指出）：
- **求值顺序反了**：global 桶排第一意味着每个请求先扣 global 再判窄桶——
  单客户端（一个前端重试 bug 就够）能把 global 120/min 打空,全员真实上报
  整分钟被丢。改为窄桶在前、global 殿后：被窄桶拒掉的请求碰不到 global。
  测试钉住：per-IP 拒绝的请求不得消耗 global 配额。
- **XFF 首跳是调用方填的**：本部署链路 client→caddy→nginx 两跳都是**追加**
  （nginx $proxy_add_x_forwarded_for、caddy 无 trusted_proxies）,首跳伪造
  即换桶。改为从右数第 `_TRUSTED_PROXY_HOPS`(=2) 个（edge 亲手追加的那个）;
  链条短于预期（本地/单条伪造）回落 socket peer,绝不信调用方文本。
- 丢弃汇总窗口语义修正：last_log 进程启动即打戳（首个丢弃不再伪装成整窗
  汇总）,日志带实际跨度;`monotonic` 提到模块顶。

### 2026-08-05 R4（review 修正）：per-IP 桶置顶——key 可信且有界的排最前

R3 把 email 桶排第一,但 allow() **拒绝时也会分配 key 的 deque**——未认证
洪水每请求换一个合法格式邮箱,就每请求在 _deques 落一个新 key（O(n)
cleanup 在共享 event loop 上）。旧顺序里这是被 global 短路挡住的,重排时
丢了这层。终版顺序=职责列表=求值顺序：**per-IP（key 由 edge 追加,不可
伪造、单攻击者一个 key,给身后一切封顶）→ per-email → global 殿后**。
测试钉住两个不变量：per-IP 拒绝不消耗 global、不在 email 桶分配新 key。
_TRUSTED_PROXY_HOPS 加 env 覆盖（FUNNEL_TRUSTED_PROXY_HOPS,默认 2）——
它是钉进应用代码的部署拓扑常量,caddy/local/ 的 per-env 路由在本仓视野外,
加/减一跳必须同步,否则 per-IP 静默塌成第二个全局桶;注释改为只依赖跳数
（append 或 overwrite 语义下从右数都成立）。丢弃计数残留挂到下个窗口的
行为维持现状（review 认可,span 如实标注）。

### 2026-08-05 R5+R6（review 修正）：HOPS 钳制下界 + 桶顺序两条不变量说实话

- `FUNNEL_TRUSTED_PROXY_HOPS` 经 `_parse_trusted_proxy_hops` 钳制 ≥1：
  hops=0 时 `parts[-0]==parts[0]`（调用方写的首跳）且 `len>=0` 恒真——
  回落保护永不触发,空 XFF 直接 IndexError 500;0 恰是「无代理部署」最自然
  的填法。空串/垃圾值回默认 2,不在 import 期炸 backend（executor_reaper
  先例）。解析函数纯化（raw: Optional[str]）,6 个边界有测试。
- `_rate_limiter.allow()` 拒绝路径不再分配 key——**防御性保证,不是封顶**
  （R5 曾把它写成"病根修复",R6 按 review 纠正）：limit>0 下新 key 永远
  被放行,放行即分配,key 增长在放行路径。**桶顺序承载两条不变量,都是
  load-bearing**：① per-IP 置顶 = caller-chosen key 分配的唯一封顶
  （每 IP 每窗口 ≤30 个 email key,重排即 R3→R4 回归复发）;② global
  殿后 = 预算不变量。test_ip_bucket_rejection_allocates_no_caller_chosen_keys
  就是①的 order guard——重排会红,红的是重排不是测试。
- 运维反向指针：deploy 仓 nginx.conf/Caddyfile 侧加注释指回本常量（deploy
  仓单独 commit,本仓注释已互指并标明该文件不在本仓）。
