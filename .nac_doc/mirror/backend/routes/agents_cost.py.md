---
code_file: backend/routes/agents_cost.py
last_verified: 2026-04-10
stub: false
---

# agents_cost.py — LLM 调用费用统计路由

## 为什么存在

每次 Agent 调用 LLM 时，`xyz_agent_context.utils.cost_tracker` 会把 token 消耗和费用记录到 `cost_records` 表。这个路由把这些原始记录聚合成前端可直接渲染的报表：总费用、按模型分类、按日期趋势，以及最近 N 条原始记录。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端费用面板
- **依赖谁**：
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `cost_records` 表
  - `xyz_agent_context.utils.cost_tracker.calculate_cost` — 导入但本路由里实际上没有调用，记录的费用在写入时已经由 cost_tracker 计算好

## 设计决策

**服务端聚合而非前端聚合**

把按模型分类和按日期的聚合逻辑放在 Python 里而不是让前端做。理由是聚合结果量级固定（最多 90 天），不会随记录数增加而变大，而且可以避免把大量原始记录传给前端再聚合的带宽浪费。

**`_all` 魔法 agent_id**

agent_id 传 `"_all"` 时查询所有 agent 的费用记录，用于管理视图。这是一个约定好的魔法值，没有用独立的查询参数来表达"查所有"。如果将来 agent_id 验证变严格，这个特殊值需要注意豁免。

**cutoff 在 Python 里计算**

时间窗口的截止时间（`cutoff`）用 Python 的 `datetime.now(utc) - timedelta(days=days)` 计算后转字符串，而不是用 SQL 的 `NOW() - INTERVAL N DAY`。这是为了同时兼容 SQLite 和 MySQL，因为两者的日期函数语法不同。

## Gotcha / 边界情况

- **`created_at` 字段的类型不一致**：SQLite 返回字符串，MySQL 返回 `datetime` 对象。`day_str` 的计算需要区分处理：字符串用切片取前 10 位，datetime 对象用 `strftime`。如果这个字段的类型处理不对，`daily` 聚合会出错。
- **`calculate_cost` 导入但未使用**：`from xyz_agent_context.utils.cost_tracker import calculate_cost` 出现在 import 里但路由函数里没有调用。这是无用导入，可以清理。

## 新人易踩的坑

`days` 参数控制查多久的记录，最大 90 天。如果想查更长时间的历史，需要修改参数范围校验 `le=90`。`limit` 参数只影响返回的原始记录数量，不影响聚合计算（聚合对所有时间窗口内的记录进行）。
