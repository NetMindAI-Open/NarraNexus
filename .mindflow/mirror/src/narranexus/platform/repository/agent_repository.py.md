---
code_file: src/narranexus/platform/repository/agent_repository.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17（更正）— 「唯一汇聚点」这句话是错的

下面那条原文写着 `add_agent` 是「全部 5 条创建路径唯一都经过的点」、归一「因此
不是调用方的自由,是表的性质」。**第一句是假的**,第二句因此当时也不成立 ——
`agents` 行有 4 个绕过本 repository 的直写点（review 抓到,已核）：

```
git grep -nE '(insert|update)\(\s*"agents"|_ins\("agents"' -- backend src
  backend/routes/manyfold/agents.py:187   POST /manyfold/agents 幂等分支
  backend/routes/manyfold/agents.py:194   POST /manyfold/agents 新建分支
  backend/routes/manyfold/agents.py:300   PATCH /manyfold/agents/{id} —— 改名端点
  src/narranexus/platform/bundle/importer.py:813  bundle / team marketplace 导入
```

讽刺的是仓库自己记着这件事：`tests/backend/test_agent_request_length.py` 的注释
就写着「Manyfold write path（the 4th path）… These raw-write the agents row」。
写下 only/always/never 之前先 grep 反例 —— 这次没做。

**修法是收口,不是把话说窄**：4 处全部在自己的写边归一（manyfold 三处走
`normalize_agent_row_text`，importer 在 dedupe 与 clamp **之前**跑
`normalize_agent_text`，理由见那里的注释：不先归一的话带空白的名字既不会与库里
已归一的同名行去重，dedupe 追加的 " (n)" 还会把 255 顶过上限）。

不变量的正式陈述搬到了 [[entity_schema]] `agent_field_matches` 的 docstring 上，
并附上那条 grep 命令 —— 下一个人可以自己重验，而不是相信一句陈述。
测试：`tests/backend/test_agents_row_writers_normalize.py`。

## 2026-08-17 — 入库前归一 agent_name / agent_description

`add_agent` 与 `update_agent` 现在都对两个文本字段跑 [[entity_schema]] 的
`normalize_agent_text`(首尾空白剥掉,`None`≡`""`)。

**为什么落在 repository 而不是各调用方**:它是全部 5 条创建路径唯一都经过的点 ——
`POST /api/auth/agents`、social-network 建 agent 路由、MCP `create_agent` 工具三条
走 [[provision]] `provision_new_agent`,arena 供给与迁移 applier 则直接调 `add_agent`
(applier 绕过了 provision,所以"放 provision 里"覆盖不到)。

**不归一会怎样**:改名路径([[auth.py]])比较的是归一后的值。所以一行存着
「小绿␣」的 agent,owner 在侧栏把它改成「小绿」时会被判"没变化"→ 一次写都不发 →
**这行永远归一不了**。存进来的形态因此不是调用方的自由,是表的性质。

调用方那一半仍有各自的责任:`or "New Agent"` 这类默认串必须在**归一之后**判
(`"   "` 是 truthy,先 `or` 就漏过默认值,存下纯空格名 → 侧栏行标题空白)。
见 [[auth.py]] / [[social_network.py]] / [[store.py]] 各自那一条。

## 2026-08-10 — resolve_owner 区分 ""(不存在) 与 None(查询失败)

PR #258 review #4:这个值现在支撑 7 个 route 家族的授权判定,「agent 不存在」
和「数据库抖了」不能塌缩成同一个答案——否则一次 DB 故障表现为一批用户的
agent 集体"消失",且没有任何 5xx 指标可告警。except 路径改返 None;只按
truthiness 用的调用方(circuit_breaker/local_bus/managed_ingress/mcp_auth
的正例缓存等)不受影响(两者皆 falsy),授权调用方(backend/routes/
_ownership.py)把 None 映射为 503。`last_verified` 同步。


## 2026-07-31 — resolve_owner：agent 属主语义的唯一出口

新增 `resolve_owner(agent_id) -> str`（''=未知/失败）。此前同一逻辑有
三份私有拷贝（channel_trigger_base._resolve_agent_owner /
message_bus_trigger._get_agent_owner / openai_compat.
_resolve_agent_creator），三处已全部收敛为对本方法的委托；第四个消费者
是观察端点的可见性判定（websocket.py）。刻意与 `events.user_id` 区分：
那列是 run 的触发方 key（team run 存发送方），不是属主。

# agent_repository.py

## Why it exists

`AgentRepository` is the only sanctioned path to the `agents` table. Agent records are created by the API, updated by the settings panel, and read by every flow that needs the agent's name, description, or public visibility flag. Centralizing this access prevents the `agents` table from being queried ad-hoc across the codebase.

## Upstream / Downstream

Agent management routes in `backend/routes/` create and update agents via this repository. `BasicInfoModule.gather()` reads `agent_name`, `agent_description`, and `created_by` to populate `ContextData`. Auth middleware reads agent records to verify ownership. The entity model is `schema.entity_schema.Agent`.

## Design decisions

**`id_field = "id"`** (the auto-increment integer) rather than `"agent_id"`: the `agents` table was designed with an auto-increment `id` as the primary key; `agent_id` is a business identifier in a VARCHAR column. Because `id_field = "id"`, `BaseRepository.get_by_id()` is effectively useless here — it would query by the numeric ID. The repository exposes `get_agent()` instead, which queries by `agent_id`.

**`update_agent()` builds raw SQL**: the base class `update()` uses `id_field` (= `"id"`, the integer) but we need to update by `agent_id` (the business key). This is the pattern used throughout the codebase whenever the update condition differs from the base class's assumption.

## Gotchas

**`is_public` stored as integer 0/1 in MySQL**: `_entity_to_row()` converts `bool` to `int(entity.is_public)` on write, and `_row_to_entity()` converts via `bool(row.get("is_public", 0))` on read. Raw integer `1` from a DB cursor is not the same as Python `True` for strict equality checks.

**`bootstrap_active` does not exist in the `agents` table**: it is computed at request time by checking the AwarenessModule state. Do not look for it in this repository.

## New-joiner traps

- Calling `repo.get_by_id("agent_abc123")` will query `WHERE id = 'agent_abc123'` (integer column, string argument) and silently return `None`. Always use `repo.get_agent("agent_abc123")`.
- There is no `delete_agent()` method here. Agent deletion is a cascade operation in the route handler that touches many tables. It cannot safely be handled through a single repository call.
