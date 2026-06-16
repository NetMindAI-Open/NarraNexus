---
code_file: backend/routes/admin_migration.py
last_verified: 2026-06-12
stub: false
---

# admin_migration.py — 单用户身份迁移 HTTP 端点

## 为什么存在

离线批量迁移脚本（`scripts/migrate_users_to_netmind.py`）适合停服窗口一次性处理全量用户，但后续场景需要更细粒度的入口：某个用户在批量迁移前已用 NetMind 注册（无旧账号），或需要把旧 id 换绑到不同的 NetMind hex。本路由提供一个单用户原子化的 HTTP 接口，让运维人员或批量脚本逐条调用，同时也支持后续随时换绑的运营操作。

独立成一个路由文件（而非合并进 `admin_quota.py`），是因为迁移操作的鉴权模式、调用者、风险等级与 quota 管理完全不同——它改写的是用户主体 id，而非额度数字。铁律 3（模块独立）。

## 这个文件不做什么

不负责批量遍历用户；批量迁移由 CLI 脚本逐条调用本接口或直接调内核实现。不解析 CSV 映射文件。不验证 `to_power_hex` 是否在 NetMind 侧真实存在——调用方负责确保 hex 来自 NetMind 批量建号回传。

## 上下游关系

**被谁用**：
- `scripts/migrate_users_to_netmind.py`（停服窗口批量模式）：`_amain --execute` 逐条 POST 本接口，或直接调 `identity_migration.execute_migration`（二选一，脚本层决策）。
- 运维人员：curl / Postman 换绑单个用户，停服窗口内使用。
- `backend/main.py`：`app.include_router(admin_migration_router, tags=["AdminMigration"])`，router 自带 prefix `/api/admin`，最终路径 `POST /api/admin/migrate-identity`。

**依赖谁**：
- `xyz_agent_context.services.identity_migration.execute_migration`：实际迁移内核。
- `backend.config.settings`：读 `settings.admin_secret_key`（鉴权依据）。
- `backend.auth.AUTH_EXEMPT_PATHS`：本路由加入豁免列表，不走 JWT middleware。

## 设计决策

- **X-Admin-Secret 替代 JWT**：本端点能把任意用户数据迁移到任意 hex，是极高危操作。普通 JWT（包括 staff 角色）不作为鉴权凭证——离线批量迁移脚本没有 JWT，且 JWT 持有者不应能执行 id 改写。选用独立的 `X-Admin-Secret` header 匹配 `settings.admin_secret_key`，与 `admin_logs` 等后台接口一致。未配置 secret→503，header 缺失或错误→403。
- **加入 `AUTH_EXEMPT_PATHS`**：与 `/api/auth/netmind-login`、`/api/invite/internal/issue` 同属"自带凭证、不走 JWT middleware"模式。若不豁免，JWT middleware 会在 header 验证前先拦截并返回 401（离线批量脚本没有 JWT）。
- **单用户原子化**：每次请求只迁一个用户（`from_user_id` → `to_power_hex`），内核保证单事务。批量由调用方循环驱动，单条失败不影响其他用户，便于断点续跑。
- **`from_user_id` 接受旧 id 或已迁 hex**：允许对已迁用户做换绑（hex→新 hex）。内核的幂等逻辑（旧 id 行不存在则 skip）兼容两种输入。
- **可选 power_email / power_display_name**：批量迁移场景下 NetMind 回传的 CSV 可能含用户信息，路由层接受这些字段并顺带 `UPDATE users` 的对应列，省去运维额外操作。不传则不更新。

## Gotcha / 边界情况

- **触发**：`settings.admin_secret_key` 未在环境变量里配置 → **症状**：所有 `POST /api/admin/migrate-identity` 请求返回 503 → **根因**：`_require_admin_secret` 在 secret 为空时直接 503（"功能未启用"语义），防止用空 secret 匹配空 header 绕过鉴权。
- **触发**：`to_power_hex` 传入的字符串不足 32 位或含非 hex 字符 → **症状**：返回 400，消息说明格式要求 → **根因**：路由层主动校验，不依赖内核的 DB 约束错误来暴露格式问题。
- **触发**：停服窗口内并发调用同一 `from_user_id` 的两个请求 → **症状**：第一个成功后第二个 skip（幂等）→ **根因**：内核以「旧 id users 行存在」为门，第一个事务提交后旧行被改写，第二个请求检测不到旧行直接返回 skipped。停服前提确保无并发业务请求，但批量脚本的并发调用是安全的。

## 新人易踩的坑

修改 `AUTH_EXEMPT_PATHS` 时如果漏掉 `/api/admin/migrate-identity`，云模式下 JWT middleware 会先于本路由的 `_require_admin_secret` 检查运行，离线批量脚本没有 JWT 会收到 401，看起来像鉴权配置问题但根因是豁免列表漏项。本地模式不受影响（JWT middleware 在 local 模式不生效），所以这类 bug 只在云模式复现。

## 相关约束

- 铁律 #3 —— 本路由只依赖 `identity_migration` 内核，不 import `scripts.*`。
- 铁律 #8 —— 迁移逻辑单一来源在 `identity_migration.py`，本文件只做 HTTP 适配层。
- v1.7.16 教训 —— 本路由的处理时间可能较长（单用户迁移涉及多表），但属于停服窗口主动调用，不存在影响正常请求的风险。
