---
code_file: backend/routes/auth.py
last_verified: 2026-05-21
stub: false
---

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
