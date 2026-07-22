---
code_file: backend/routes/auth.py
last_verified: 2026-07-17
stub: false
---

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

The last three routes that trusted a client-supplied user id now derive identity from auth_middleware via `resolve_current_user_id`: POST /agents (body created_by removed — clients could create agents under anyone's account), POST /timezone and GET+POST /onboarding (body/query user_id removed). Old clients sending the extra field are harmless (pydantic ignores unknown fields); old clients omitting X-User-Id/JWT get 401. scripts/bench_narrative_models.py updated to send X-User-Id.

## 2026-06-11 — legacy cloud auth removed (invite codes retired)

/login is local-only now (cloud -> 404, points at netmind-login); /register deleted outright; /create-user gained a cloud 404 guard (it was an unauthenticated open account-creation endpoint sitting in AUTH_EXEMPT_PATHS — known hole, now closed). Invite-code mechanism retired entirely per 2026-06-10 owner decision (signup == first NetMind login, everyone gets the free-tier quota): routes/invite.py and routes/admin_invite.py deleted, InviteCodeRepository and invite_code_gen deleted, INVITE_AUTO_ISSUE_CAP / INTERNAL_INVITE_SECRET config gone. The invite_codes TABLE survives — it holds the old-user-id -> email mapping the legacy-user migration script needs.

## 2026-06-11 — POST /api/auth/netmind-login (Phase 1 user-system unification)

New cloud-only login endpoint: verifies a NetMind loginToken via `NetmindAuthClient` (one network call to NetMind's /user/balance), lazily upserts the local user (`UserRepository.upsert_netmind_user`, user_id = NetMind userSystemCode), seeds the free-tier quota on FIRST login (registration no longer exists — first login is registration; invite codes are gone per 2026-06-10 decision), then issues NarraNexus's own JWT. Error mapping: bad token -> 401, NetMind unreachable/contract drift -> 502 (never disguised as a credential failure). `_get_netmind_auth_client()` is module-level for test monkeypatching. The legacy /login (cloud password branch) and /register are slated for removal in the same feature branch.

last_verified: 2026-06-10
stub: false
---

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
设计文档：`drafts/logs/invite_code_2026_05_14.md`。

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
