---
code_file: src/xyz_agent_context/repository/instance_repository.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4d：get_public_instances 补 order_by（此前是唯一没有排序的查询）

`get_public_instances()` 原来是 `self.find(filters=filters)`，**没有 order_by**——
本文件里其余三个查询（`get_by_agent`、`get_by_agent_and_user`、
`get_chat_instances_by_user`）全都显式指定了 `created_at DESC`，只有它漏了。
返回顺序因此是"引擎高兴怎么给就怎么给"，而这个顺序会一路变成 active_instances
→ system prompt 里的 module 块顺序（[[context_runtime.py]]）。SQLite 恰好回
rowid 序所以本机看不出问题，**Postgres/MySQL 不承诺任何顺序**，cloud 上一次
执行计划切换就会重排同优先级的 module 块——等长重排，缓存前缀断裂，字节计数
诊断无感。

- 现在 `order_by="created_at DESC"`，与同类方法约定一致。
- **只能给一列**：backend 的 order_by 解析器
  （`db_backend_sqlite.get` / `db_backend_mysql.get` / `database.get`）只校验
  **一个标识符 + 一个 ASC/DESC token**，写成 `"created_at DESC, instance_id ASC"`
  会被静默降级成 `ORDER BY "created_at"`（升序！）——语义悄悄反转。想要多列排序
  必须先改 backend 解析器，不要在调用点塞逗号。
- prompt 层的确定性**不依赖**本条：ContextRuntime 用
  `(priority, module_class)` 全序重排 module 块。这里补 order_by 是为了消除
  该列表其他消费者（instance_factory 的 agent-level instances 等）的潜在不确定性。
- 测试：`tests/context_runtime/test_module_block_order.py::
  test_get_public_instances_issues_an_order_by` +
  `..._order_by_is_a_single_sortable_column`（用记录型 fake db 断言下发的
  order_by 参数）。

# instance_repository.py

## Why it exists

`InstanceRepository` manages the `module_instances` table — the registry of all active, completed, and archived module instances across all agents. It is the data layer for Step 2 of `AgentRuntime` (loading candidate instances for selection) and for `ModulePoller` (polling for state transitions). It also implements the in-process vector similarity search used for semantic instance retrieval.

## Upstream / Downstream

`ModuleService._module_impl/` calls `get_by_agent_and_user()` and `vector_search()` to find candidate instances for the current turn. `ModulePoller` polls via `get_by_agent()` filtered on status `in_progress`. `InstanceNarrativeLinkRepository` is the companion repository — instance-narrative links are stored separately and loaded on top of `ModuleInstanceRecord` at runtime.

## Design decisions

**`id_field = "instance_id"`**: unlike `AgentRepository` and `AgentMessageRepository` where `id_field = "id"` creates a mismatch, here `instance_id` is both the business key and the field used as the primary lookup key. `BaseRepository.get_by_id("chat_a1b2c3d4")` works correctly.

**`get_by_agent_and_user()` uses raw SQL** with `(is_public = 1 OR user_id = %s)`: the base class `find()` only supports equality filter dicts. An OR condition requires raw SQL. This is a clean, deliberate bypass.

**`vector_search()` loads all candidates and computes cosine similarity in Python with `numpy`**: MySQL has no native vector index. The decision was to keep it simple and pay the deserialization cost. For small-to-medium agent setups (< a few thousand instances), this is acceptable. At scale it would need a vector database.

**`get_chat_instances_by_user()` explicitly hardcodes `module_class = 'ChatModule'`**: this is a specific query for the "dual-track memory loading" feature (P1-2, January 2026). It retrieves all ChatModule instances for a user across all narratives to load short-term memory from recent non-current conversations.

## Gotchas

**`vector_search()` does not apply `status_filter` before loading candidates**: it first loads all instances for the agent+user via `get_by_agent_and_user()`, then filters by status in Python. For agents with many archived instances, this is wasteful. The SQL queries do not push the status filter to the database.

**`routing_embedding` is stored as JSON and loaded on every `find()` call**: even queries that don't need embeddings (e.g., `get_by_agent()` to check statuses) will deserialize 1536-float lists for every instance that has an embedding. There is no lazy-loading — the full entity is always loaded.

**`update_last_used()` formats the time as a string**: `utc_now().strftime('%Y-%m-%d %H:%M:%S')`. Other repositories also do this. If `utc_now()` has timezone info and the database column expects naive datetime, this formatting strips the tz offset. Verify that the format matches what MySQL expects in your environment.

## New-joiner traps

- `InstanceRepository` returns `ModuleInstanceRecord` objects (no live module bound). Callers that need the live module object must bind it separately — the `ModuleService` does this after loading from the repository.
- `callback_processed` and `last_polled_status` are poller-internal fields stored in the same table. Application code (modules, routes) should never read or write these directly — they are owned by `ModulePoller`.
