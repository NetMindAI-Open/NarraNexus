---
code_file: backend/routes/auth.py
last_verified: 2026-04-10
stub: false
---

# routes/auth.py — 用户认证与 Agent CRUD 路由

## 为什么存在

这个文件承担了两个职责：用户认证（登录、注册）和 Agent 的完整生命周期管理（创建、更新、删除、列表）。Agent CRUD 放在 auth 路由下而不是 agents 路由下，是因为这些操作需要用户身份验证（"这个 agent 属于谁"），在概念上更接近用户管理而非 agent 资源操作。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(auth_router, prefix="/api/auth")`；前端登录页、Agent 管理页
- **依赖谁**：
  - `AgentRepository` — Agent 的基础 CRUD
  - `UserRepository` — 用户的增删查、last_login 更新、timezone 更新
  - `backend.auth` — `hash_password`、`verify_password`、`create_token`、`_is_cloud_mode`、`INVITE_CODE`
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
