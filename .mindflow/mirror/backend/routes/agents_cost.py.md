---
code_file: backend/routes/agents_cost.py
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — local 多用户隔离修复

`_resolve_viewer_id` 改走统一 helper
`backend.auth.resolve_current_user_id`——cloud / local 共享同一个
identity 通路。query-param 拒绝逻辑保留（防御 TDR-12 spoof）。

# agents_cost.py — LLM 调用费用统计路由

## 为什么存在

每次 Agent 调用 LLM 时，`xyz_agent_context.utils.cost_tracker` 会把 token 消耗和费用记录到 `cost_records` 表。这个路由把这些原始记录聚合成前端可直接渲染的报表：总费用、按模型分类、按日期趋势，以及最近 N 条原始记录。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端 `CostPopover`
- **依赖谁**：
  - `backend.auth._is_cloud_mode` / `get_local_user_id` — 拿当前 viewer 身份
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `cost_records` / `agents` 表

## 设计决策

**服务端聚合而非前端聚合**

把按模型分类和按日期的聚合逻辑放在 Python 里而不是让前端做。理由是聚合结果量级固定（最多 90 天），不会随记录数增加而变大，而且可以避免把大量原始记录传给前端再聚合的带宽浪费。

**`_all` = 当前 viewer 拥有的所有 agent，而非全表**

agent_id 传 `"_all"` 时，先按 `agents.created_by = viewer_id` 列出该用户名下所有 agent，再用 `WHERE agent_id IN (...)` 限定 cost 查询范围。理由：

- `cost_records` 表只有 `agent_id` 没有 `user_id`，多租户隔离必须靠 JOIN/IN-list
- "公开 agent" 即使能被别人看到，cost 也是创建者付的，把它算入 viewer 的总账等于泄露所有者花销
- 之前版本在云端会把 **全平台所有用户** 的 cost 一锅端给随便哪个登录用户

**单 agent 也强制所有权检查**

`agent_id != "_all"` 分支会先查 `agents.created_by` 验证所有权，命中失败统一返回 404（不是 403），避免泄露 agent 是否存在。这是 defense in depth，前端不会触发，但防直接打 API。

**Viewer 身份只信 session，不信 query param**

参照 `dashboard.py` 的 TDR-12 决策：`?user_id=` 来的值会被直接 400 拒绝。云端从 `request.state.user_id`（JWT 中间件填）读，本地从 `get_local_user_id()` 读。

**cutoff 在 Python 里计算**

时间窗口的截止时间用 Python 的 `datetime.now(utc) - timedelta(days=days)` 计算后转字符串，而不是用 SQL 的 `NOW() - INTERVAL N DAY`，兼容 SQLite 和 MySQL。

## Gotcha / 边界情况

- **`created_at` 字段的类型不一致**：SQLite 返回字符串，MySQL 返回 `datetime` 对象。`day_str` 的计算需要区分处理：字符串用切片取前 10 位，datetime 对象用 `strftime`
- **`HTTPException` 在 try 块里必须 re-raise**：聚合 `try/except Exception` 包住整个主流程拿 500 兜底，但 401/400/404 这种 HTTPException 必须 `except HTTPException: raise` 透传，否则会被吞成 `success=False, error=...` 的 200 响应
- **`agent_id IN (...)` 的展开**：占位符必须随 owned_ids 长度动态生成 `%s,%s,...`，不能用单 `%s` 然后传 list

## 新人易踩的坑

`days` 参数控制查多久的记录，最大 90 天。如果想查更长时间的历史，需要修改参数范围校验 `le=90`。`limit` 参数只影响返回的原始记录数量，不影响聚合计算（聚合对所有时间窗口内的记录进行）。

如果 viewer 名下一个 agent 都没有，`_all` 直接返回空 summary，不会跑 SQL，不会出错。
