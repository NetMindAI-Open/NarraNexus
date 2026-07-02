---
code_file: src/xyz_agent_context/repository/executor_audit_repository.py
last_verified: 2026-06-18
stub: false
---

# executor_audit_repository.py — executor/loop lifecycle + OOM 事件审计日志

## 为什么存在

容器重启会清空 Docker 日志；DB 不会（incident lesson #5）。每次发生 OOM 或失控
循环后，事后排查的核心问题是"executor 在崩溃前 10 分钟在做什么"——这张表就是
答案。面向运营/监控的两个读路径：

- `recent()` — 管理后台 / 人工排查用的最新 N 行
- `counts_since()` — L3 监控用的事件类型计数（窗口内 OOM 激增时报警）

## 这个文件不做什么

不继承 `BaseRepository`——与 `ServiceAuditRepository` 和 `LarkTriggerAuditRepository`
保持一致的设计：这是 append-only 日志，不需要 get_by_id / upsert / save 等通用 CRUD，
继承 BaseRepository 只会带来无用的 abstract method 要求。

## 上下游关系

- **被谁用**：executor 调度层（`agent_runtime/` 内将来的 executor_manager）在
  container_start / cull / OOM 等关键节点调用 `record()`；监控/healthz 端点调用
  `recent()` 和 `counts_since()`。
- **依赖谁**：`schema/executor_audit.py`（导入 `ExecutorAuditEvent` 用于文档/类型）、
  注入的 db_client（无类型，与 ServiceAuditRepository 相同约定）。

## 设计决策

- **best-effort writes**：`record()` 绝不抛异常。审计观察者不能破坏被观察的执行者；
  丢一行审计记录好过 stall 一个 executor 循环。
- **db_client 无类型注入**：导入 `AsyncDatabaseClient` 会增加加载顺序耦合，没有收益，
  同 ServiceAuditRepository 约定。
- **counts_since 做 fetch-then-count 而非 GROUP BY**：`AsyncDatabaseClient` API 是
  基于 filter 的，不支持 SQL GROUP BY；窗口内行数量级小，在 Python 内聚合完全可行。
  与 `LarkTriggerAuditRepository.count_by_type()` 一致。

## Gotcha

- `counts_since` 的 `since_iso` 参数是字符串比较（ISO 格式），而 sqlite backend 返回
  的 `created_at` 可能是 `datetime` 对象。`counts_since` 内部做了 `isinstance` 判断，
  把 datetime 转成 `isoformat(sep="T")` 再比较——不能直接做 `ts < since_iso`。

## 相关约束

- 铁律 #5 — DB 审计 > 应用日志；这就是该铁律的直接产物。
- 表定义在 `utils/schema_registry.py`（`instance_executor_audit`），由 `auto_migrate`
  幂等创建，禁止手写 DDL。
