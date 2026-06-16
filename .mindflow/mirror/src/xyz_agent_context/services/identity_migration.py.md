---
code_file: src/xyz_agent_context/services/identity_migration.py
last_verified: 2026-06-12
stub: false
---

# identity_migration.py — 身份迁移内核（platform service 层）

## 为什么存在

Phase 1 用户系统统一要求把所有存量旧 user_id 改写为 NetMind 的 32-hex userSystemCode。这块逻辑最初全部住在 `scripts/migrate_users_to_netmind.py`，但有两个调用方需要它：离线 CLI 脚本（批量迁移）和 `backend/routes/admin_migration.py`（单用户原子迁移，供换绑操作）。把内核提取成独立 platform service 模块，符合铁律 3（模块独立、热插拔）和铁律 8（单一真相源，不重复），让两个调用方共享同一份实现，避免逻辑漂移。

本模块是**纯 platform service 层**，不感知 HTTP/CLI 上下文，不读环境变量，不持有状态。所有 DB 操作通过传入的 `db` 参数进行，便于测试隔离。

## 这个文件不做什么

不直接读 CSV 文件，不解析命令行参数，不做报告输出格式化——这些由 `scripts/migrate_users_to_netmind.py` 的 CLI 层负责。不读 `NETMIND_AUTH_API_URL` 也不调 NetMind API——调用方在调用前已经从 NetMind 批量建号回传的 CSV 拿到了 hex。

## 上下游关系

**被谁用**：
- `scripts/migrate_users_to_netmind.py`：import 并 re-export `classify_identity_columns`、`execute_migration`、`verify_migration`、`build_report`、`IdentityColumns`，让 CLI 的 `_amain` 函数调用。
- `backend/routes/admin_migration.py`：`POST /api/admin/migrate-identity` 路由处理函数调用 `execute_migration` 做单用户原子迁移，可选地顺带更新目标行 display_name/email。

**依赖谁**：
- `xyz_agent_context.utils.schema_registry`（`TABLES`）：`classify_identity_columns` 和 `_is_unique_identity_column` 从中自动发现所有身份列，避免列清单硬编码。
- `xyz_agent_context.utils.db_factory`：操作传入的 `AsyncDatabaseClient`。
- `pathlib.Path`：`_rename_workspaces` 操作 workspace 目录。

## 关键函数说明

**`classify_identity_columns()`**：从 `schema_registry.TABLES` 扫描身份形名字的列（`user_id` / `owner_user_id` / `created_by` / `used_by_user_id` / `scope_id`），分为「需要迁移」和「显式排除」两类。排除清单：`channel_slack_credentials.owner_user_id`、`channel_telegram_credentials.owner_user_id`（IM 平台 uid，不是 NarraNexus user id）、`bus_channels.created_by`（agent id，不是 user id）。`memory_*` 表的 `scope_id` 仅在 `scope_type='user'` 时是用户身份，作为「条件改写列」单独标记。任何未分类的新身份形列名会直接 `raise`——加新表的人必须有意识分类，防漏守卫。

**`execute_migration(db, mapping, base_working_path)`**：逐用户单事务改写所有身份列。流程：① 检查旧 id 对应的 users 行存在（幂等门：不存在则 skip）；② 检查目标 hex 行是否已存在（MERGE 判断）；③ 对唯一约束列（由 `_is_unique_identity_column` 判断：`Column.unique=True` 或参与单列 unique Index）DELETE 旧行、保留目标行；④ 多份业务数据列 `UPDATE` 并入目标 hex；⑤ `_stamp_user` 写 `users.metadata` 的 `netmind_migration` 审计标记（存 old id）；⑥ 事务提交后 `_rename_workspaces`（`{agent_id}_{old}` → `{agent_id}_{new}`）。stats 含 `users_migrated`、`users_merged`、`users_skipped`。

**`_is_unique_identity_column(table, column)`**：从 schema_registry 查该列是否在唯一约束上（`Column.unique` 或该列是某个单列 `Index(unique=True)` 的唯一成员）。`user_quotas.user_id`、`user_settings.user_id`、`users.user_id` 这类"每个用户只有一行"的配置表在 MERGE 场景下必须 DELETE 旧行，这个函数是判断依据。

## 设计决策

- **MERGE 语义**：目标 hex 行已存在意味着用户迁移前已用 NetMind 登录过，老账号有活跃数据。配置类唯一行 DELETE 旧行、保留新行（新行是权威状态）；业务数据 UPDATE 并入（agents、events、memory 等不丢数据）。被否决方案：全表 DELETE 旧行——会丢失用户已有 agents，不可接受。
- **幂等**：迁移以「旧 id 的 users 行还存在」为门，重跑自动跳过。目录改名容忍已改名（`Path.rename` 只在源存在且目标不存在时执行）。崩溃恢复 = 重跑一次。
- **BINARY 比较**：与 `UserRepository` 一致，user_id 大小写敏感，SQL 中用 `BINARY` 比较避免 MySQL 的 `utf8mb4_unicode_ci` 大小写折叠。
- **离线停服跑**：本模块本身不限制运行时机，但注释明确要求调用方（CLI 脚本和 admin 路由）只在停服窗口内调用。v1.7.16 事故教训：耗时迁移进 lifespan 导致 prod 下线 20 分钟。本模块**绝不**被 lifespan 或任何请求链路调用，只被显式的停服迁移入口调用。

## Gotcha / 边界情况

- **触发**：新增了一个表，列名含 `user_id`/`owner_user_id`/`created_by`/`used_by_user_id`/`scope_id`，但没有更新 `classify_identity_columns` 的排除清单 → **症状**：下次迁移时 `classify_identity_columns` 抛 `RuntimeError`（未分类列防漏守卫）→ **根因**：函数对未知身份形列名设计为 fail-fast，而非静默包含。修法：到 `classify_identity_columns` 里把新列显式归类（迁移 or 排除）。
- **触发**：MERGE 路径中，目标 hex 行存在但某张配置表的行未在 `_is_unique_identity_column` 里被识别为唯一 → **症状**：目标行和旧行同时存在，后续查询按 PRIMARY KEY 找不到预期行 → **根因**：schema_registry 里该列未声明 `unique=True`。修法：在 schema 定义里补 unique 约束，或 `classify_identity_columns` 里显式列出该列的 MERGE 处理方式。

## 新人易踩的坑

`execute_migration` 的事务覆盖 DB 写操作，但 workspace 目录改名（`_rename_workspaces`）在事务提交**之后**执行，属于两阶段提交没有 rollback。事务失败时目录不改名（一致）；事务成功但改名失败时，数据库已是新 hex，目录还是旧名，需要手工改名或重跑（第二遍会识别 users 行已迁移，skip DB 步骤，只补目录改名）。

## 相关约束

- 铁律 #3 —— 模块独立：本模块不 import `backend.*` 或 `scripts.*`，只作为被调用方。
- 铁律 #8 —— 单一真相源：迁移核心逻辑只在此处，CLI 脚本 re-export 而非复制。
- v1.7.16 教训 —— 本模块禁止进 lifespan，停服迁移是唯一合法调用场景。
