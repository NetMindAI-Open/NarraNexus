---
code_file: src/xyz_agent_context/utils/one_shot_migrations.py
last_verified: 2026-07-13
stub: false
---

# one_shot_migrations.py

## 为什么存在

`auto_migrate` 只做 additive schema 变更（加表加列）。当协议变化需要**数据迁移**（比如"把不合新 schema 的旧行标记为 cancelled"）时，需要一个独立的幂等数据迁移层。本文件承载这类一次性函数。每个函数必须：
1. 幂等（二次运行零副作用）
2. 可安全并发（或者调用方保证串行）
3. 通过明确条件筛选目标行，不误伤

## 上下游关系

- **被谁用**：`backend/main.py` 的 lifespan startup hook，在 `auto_migrate` 之后顺序调用
- **依赖谁**：`AsyncDatabaseClient`（标准 CRUD）+ `loguru`

## 设计决策

**不用单独表记 migration state**：靠"目标条件已不再成立"做幂等性。例如 `migrate_jobs_protocol_v2_timezone` 筛选"trigger_config 无 timezone 字段"的 active/pending/paused 行——处理完它们状态就变 cancelled，下次扫描自然命中 0 行。这比加一个 `migrations_applied` 表轻量，代价是每次启动都扫一遍表（对普通规模 jobs 表可接受）。

**在 Python 侧判 JSON 字段**而非 SQL `JSON_EXTRACT`：MySQL 和 SQLite 的 JSON 函数语法不同，跨 backend 写起来要做 dialect 分支。反而在 Python 里 `json.loads + .get('timezone')` 最清晰。规模大了可以加 backend 特化的 SQL 过滤。

**cloud/local 判定必须走 SSOT（`utils.deployment_mode.is_cloud_mode`）**：`heal_legacy_singleton_ownership` 是 local-only 自愈，靠条件 1 的 cloud 判定短路。曾经这里抄了一份只看 `DATABASE_URL` 的本地副本 `_is_cloud_mode_env`，它丢掉了 `NARRANEXUS_DEPLOYMENT_MODE` 最高优先级这一层——于是纯靠 `NARRANEXUS_DEPLOYMENT_MODE=cloud`（`DATABASE_URL` 空）声明的 prod 被误判为 local，heal 每次启动都往下走，命中多用户分支狂刷 `[singleton-heal] N non-default users` 噪音日志。根因是判定逻辑漂移。`deployment_mode` 是个纯 env 叶子模块（只 import `os`/`typing`），直接引用它不会有循环 import，无需再抄副本。

## Gotcha / 新人易踩坑

- 本文件的每个函数**必须能重复调用**，不要做"全局一次"式的锁或版本号判断。简洁优于防御。
- 如果未来加一个新函数，按"每个函数一个独立迁移意图"来写，并且也要在 `backend/main.py` 的 startup 里显式调用——**不要**做"遍历本模块所有函数"的反射式调度（失控）。
- **不要再抄 cloud/local 判定副本**——任何"避免循环 import"的复制都可能悄悄漂移。统一 `from xyz_agent_context.utils.deployment_mode import is_cloud_mode`。
