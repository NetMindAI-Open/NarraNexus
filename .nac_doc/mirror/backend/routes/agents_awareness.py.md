---
code_file: backend/routes/agents_awareness.py
last_verified: 2026-04-10
stub: false
---

# agents_awareness.py — Agent Awareness 读写路由

## 为什么存在

Awareness 是 Agent 的自我认知配置——它知道自己是谁、有什么能力、适用于哪些场景。这些信息存储在 `instance_awareness` 表里，通过 `AwarenessModule` 的实例 ID 关联到 Agent。这个路由文件暴露 GET/PUT 两个接口，让前端能读取和编辑 Awareness 内容。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合并挂载到 `/api/agents`；前端 `AwarenessPanel` 组件
- **依赖谁**：
  - `InstanceRepository` — 查询或创建 `AwarenessModule` 实例
  - `InstanceAwarenessRepository` — upsert awareness 内容到 `instance_awareness` 表
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查询 `instance_awareness` 表读取结果

## 设计决策

**自动创建实例的 `_ensure_awareness_instance`**

这个路由的一个关键设计是：如果 Agent 还没有 `AwarenessModule` 实例，GET 和 PUT 请求都会自动创建一个，而不是返回 404。理由是 Awareness 对每个 Agent 来说是必要的，在 Agent 创建时就应该存在，自动补齐比强迫调用者先创建实例更好用。

这个决策的代价是：GET 请求在极端情况下会有写副作用（创建实例），打破了 HTTP 语义中 GET 应该是幂等无副作用的约定。但实际上第一次 GET 之后实例就存在了，后续 GET 不会再写，所以问题有限。

**分开 Repository 和直接 DB 查询**

写操作用 `InstanceAwarenessRepository.upsert()`，读操作用 `db_client.get_one()` 直接查表，没有通过 Repository 封装。这是轻微的不一致，但读 Repository 的实现本质上也是 `get_one`，直接调没有额外风险。

## Gotcha / 边界情况

- **Awareness 数据不存在时 GET 返回 `success=False`**：即使实例创建成功，如果 `instance_awareness` 表里还没有这个实例的记录（比如 Awareness 从未被写过），GET 会返回 `success=False, error="Awareness data not found"`，而不是空数据。前端需要处理这个情况，把它区别于真正的错误。
- **PUT 之后立即重读**：upsert 成功后会再次 `get_one` 读取刚写入的数据并返回，这是为了确保返回值反映数据库的实际状态（比如 `updated_at` 字段由数据库生成）。

## 新人易踩的坑

`instance_awareness` 表的主键是 `instance_id`，而不是 `agent_id`。必须先通过 `_ensure_awareness_instance` 拿到实例 ID，再用实例 ID 查询，不能用 agent_id 直接查 `instance_awareness`。
