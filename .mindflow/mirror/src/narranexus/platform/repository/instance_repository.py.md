---
code_file: src/narranexus/platform/repository/instance_repository.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r3 C1/I2/M3）— 「无 narrative link」只定义一次 + 两个新候选查询

- `_NO_NARRATIVE_LINK_SQL`：`NOT EXISTS (SELECT 1 FROM instance_narrative_links inl WHERE
  inl.instance_id = module_instances.instance_id)`——「narrative-less 实例」的唯一定义（任意 link_type 都算有
  link）。依赖解析分成两个不相交人群：有 link 的只由 `handle_completion`（link 状态规则）判；无 link 的只由
  narrative-free 路径与对账（依赖 status 规则）判。给 narrative-free 一侧供候选的三条查询都带这句。
- `get_blocked_page` 加该过滤（对账不再碰 narrative 绑定的 BLOCKED，I2）；M3：`LIMIT` 改 `%s` 占位。
- 新 `get_unlinked_blocked_by_agent(agent_id)`：`handle_completion_no_narrative` 的依赖方候选（原来是
  `get_by_agent(BLOCKED)`，会把 narrative 绑定实例也拿去按 status 判）。
- 新 `get_unlinked_completed_awaiting_callback(limit)`：`status IN (completed, failed) AND
  last_polled_status='in_progress' AND callback_processed = FALSE AND <无 link> ORDER BY completed_at, id
  LIMIT %s`——[[module_poller]] 发现查询的 narrative-less 半边（C1）。独立查询、独立 LIMIT，不与 narrative
  半边共用窗口，积压不会挤掉 narrative 完成事件。
锁：`tests/repository/test_instance_repository_blocked_page.py`（`test_blocked_page_excludes_instances_with_any_narrative_link`、
`test_unlinked_blocked_by_agent`、`test_unlinked_completed_awaiting_callback`）+ `_mysql` twin 两条。

## 2026-09-10（review r2 I-A）— `get_blocked_page(after_id, limit)`：BLOCKED 的 keyset 分页

跨全部 agent 取 `status='blocked' AND id > after_id ORDER BY id ASC LIMIT n`，供 [[module_poller]] 的
BLOCKED 对账逐页走完全集。用自增 `id` 当游标而非 `created_at` / OFFSET：唯一、单调、主键索引、双方言
都是整数，下一页严格从上一页最后一行之后开始，本页行被激活移出 BLOCKED 也不会让下一页跳行。新裸 SQL
（无引号标识符、`%s` 占位、LIMIT 以 `int()` 内联）配双 twin：
`tests/repository/test_instance_repository_blocked_page.py` + `_mysql`。

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
