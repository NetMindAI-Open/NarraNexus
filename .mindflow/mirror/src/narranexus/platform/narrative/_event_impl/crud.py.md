---
code_file: src/narranexus/platform/narrative/_event_impl/crud.py
last_verified: 2026-08-05
stub: false
---

# crud.py — Event CRUD（`events` 表的唯一读写实现）

## 为什么存在

`EventCRUD` 是 `EventService` 背后的私有 create / load / save / update 实现。
一次 Agent 执行 = `events` 表一行：`create()` 在 step_0 登记（此时只有
`env_context.input` 和 `anchor`，`event_log` / `final_output` 都是空的），
step_4 §4.3 再用 `update()` 把 `final_output` / `event_log` /
`module_instances` 补齐。`_parse_event_data()` 是反向的还原路径。

数据源的三级优先级写在 `load_by_id` / `load_by_ids` 里：**DataLoader >
Repository > 裸 DB client**。批量场景一定要走 `load_by_ids`——step_2 给一条
Narrative 拉几十个 Event 时，逐个 `load_by_id` 就是 N+1。

## 上下游关系

**被谁用**：`EventService`（唯一调用者），它把这里的方法转成对
AgentRuntime 的公开接口。

**依赖谁**：可选注入的 `EventRepository` / `DataLoader[str, Event]`；两者都
没注入时 `_get_db_client()` 懒加载全局工厂客户端。

## 设计决策

**没有 `duplicate()`——一次执行只能有一行。** 2026-08-05 之前这里有一个
`duplicate(original_event, narrative_id)`，供 step_4 §4.4 把同一轮对话复制进
每一条"辅助 Narrative"。它是 0802【对话时序错乱】的根因：

- 复制发生在 run 收尾，源对象是**内存里的** Event，而 §4.3 只把
  `final_output` 同步回内存、**没有回填 `event_log`**，所以每个副本行都是
  `state='completed'` + `started_at IS NULL` + `tool_call_count=0` +
  `event_log='[]'`，却带着本轮的 `final_output`（那是 agent 的独白，不是回复）；
- `created_at` 是 run **结束**的时刻，不是提问的时刻。于是从 `events` 表回放
  的界面里，同一轮对话出现最多 3 次，而且排在更新的对话下面——"已经回答过的
  老问题又冒出来"。

正确的关联方式是 **同一个 event id 追加进每条 `narratives.event_ids`**（列表
列，多对多天然放在这一侧），`events.narrative_id` 保持单值、指向这轮被写入的
那一条线程。见 [[step_4_persist_results]] §4.4 与 [[event_service]]。

**`_parse_event_data()` 里的三处 legacy 修补**（补 `agent_id`、跳过缺
`module_class` 的项、用 `md5(event_id + module_class)` 造确定性
`instance_id`）是为了让早期没有这些字段的行还能读出来。确定性哈希这点是刻意
的：同一行每次加载得到同一个占位 id，不会每次都变。

## Gotcha / 边界情况

- `save()` 写的是**全字段 insert**，不是 upsert；重复调用会撞
  `idx_events_event_id` 唯一索引。改状态一律走 `update()`。
- `save()` 只写 `event_id / trigger / trigger_source / narrative_id /
  agent_id / user_id / env_context / module_instances / event_log /
  final_output` 这 10 列。生命周期列（`state / started_at / finished_at /
  tool_call_count / current_stage`）不在这里写——它们由运行时的
  run-recorder 侧维护，所以**一个只经过 `create()` 的行天生
  `started_at IS NULL`**。判断"这行是不是脏数据"不能只看 `started_at`。
- `load_by_ids()` 返回 `List[Optional[Event]]`，缺失位置是 `None` 而不是
  被跳过；按 index 对齐时必须自己处理 `None`。
