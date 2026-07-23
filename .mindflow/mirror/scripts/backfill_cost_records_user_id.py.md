---
code_file: scripts/backfill_cost_records_user_id.py
last_verified: 2026-07-22
stub: false
---

# backfill_cost_records_user_id.py

## 为什么存在

`cost_records.user_id` 是新加的列（见 [[schema_registry]] 2026-07-22）：从此每笔
计费记录写入时就带归属。列加上之前的历史行 `user_id` 为 NULL，只能靠
`cost_records.agent_id → agents.created_by` 反查用户。这个脚本把那条链走一次，
把答案冻结进新列。

## 诚实的边界（关键）

`agents` 是**硬删**（无软删列），删 agent 会级联删掉 `events` /
`module_instances` / `bus_agent_registry` —— 所有曾经持有 agent→owner 映射的表。
所以 agent 已不存在的 `cost_records` 行是**真孤儿**：其归属在整库里已无任何存活
副本，**不可恢复**。脚本只回填 agent 仍在的行，并如实报告孤儿数，**绝不臆造归属**。

## 设计决策

**关联子查询而非 UPDATE...JOIN**：SQLite 不支持 `UPDATE ... JOIN`，用
`SET user_id = (SELECT created_by FROM agents WHERE agents.agent_id =
cost_records.agent_id)` 双后端通吃。

**`user_id IS NULL` 守卫 → 幂等**：只碰仍为空的行，可放心重跑。

**dry-run 默认，`--apply` 才写**：默认只打印「可回填 / 孤儿」计数；`--apply` 才执行
UPDATE 并打印回填行数 + 残余孤儿数。属系统写操作，须 Owner 授权后在 EC2 上手动跑。

**不并入 `auto_migrate`**：`auto_migrate` 是 additive schema-only；数据变更必须独立、
可审计，不能塞进每次进程启动的迁移路径里。

## Gotcha / 边界情况

- `provider_source` 历史值**无法回填**：它以前只活在 ContextVar 里，从未落库，无从
  重建。回填只管 `user_id`。
- 真孤儿回填后仍是 NULL —— 这是正确结果，不是 bug。要彻底不再产生孤儿，靠的是
  [[cost_tracker]] 在写入时就存 `user_id`（止血），而非这个回填（历史清创）。

## 何时跑

`cost_records.user_id` 列上线、`auto_migrate` 应用之后，Owner 授权下在 EC2 跑一次
（先 dry-run 看计数，再 `--apply`）。把两次输出贴进 commit / 运维记录留审计痕迹。
