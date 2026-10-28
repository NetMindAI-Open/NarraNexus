---
code_file: src/narranexus/platform/repository/artifact_repository.py
last_verified: 2026-08-20
stub: false
---

## 2026-08-07 — 三个界面，三种答案（team 维度落地）

artifact 有了 `team_id` 后，「列出 artifact」不再是一个问题而是三个，且**失败方向相反**：

| 方法 | 面向 | 语义 | 泄漏方向 |
|---|---|---|---|
| `list_by_session` / `list_pinned` | 私聊面板 | `team_id IS NULL`，纯私有 | 漏加过滤 → 团队产出出现在一对一会话里 |
| `list_by_team` | 团队面板 | 该 team 全部，**不按 agent 过滤** | 越界 → 一个 team 看到另一个的 |
| `list_for_agent_context` | agent prompt | 私有 ∪ **所属全部 team** | **反向泄漏**：查窄了不报错、只是 agent 永远不知道队友的产出存在，接力静默失效 |

反向泄漏是这里最危险的一种：它 fail-closed、看起来无害，却让共享工作台形同虚设。两个方向
都必须有测试（`tests/repository/test_artifact_repository_team_scope.py`）。

**成员关系子查询写在 Repository 内、不交给调用方**：并集必须按 `team_members` 取，不能按
owner user 取——一个 user 拥有多个 team，按 user 取等于把每个 team 的产出发给该 user 的
所有 agent。放在这里是为了让调用方**没有机会传错**。

`list_by_user`（Settings 管理表）**有意不加 team 过滤**：那是「我拥有的一切」的管理视图，
团队 artifact 同属该 user，收窄反而会藏掉用户需要负责的东西。

## 2026-08-07 — list_pinned：按 updated_at 倒序 + 可选 limit

`list_pinned` 从 `find()` 改为裸 SQL，加 `ORDER BY updated_at DESC` 与可选 `limit`。
排序不是装饰：更新路径是「重新注册同一个 artifact」，会刷新 `updated_at`，因此
「最新」正好等于「agent 当前在迭代的东西」，取头部即工作集。

**limit 缺省必须是 None（全量）**：[[profiles.py]] 的 bootstrap 去重扫的就是这个列表，
一旦默认截断，profile artifact 会被判定为不存在而重复创建。截断只能是调用方显式要求。
唯一的截断调用方见 [[common_tools_module.py]]。


## 2026-07-21 — update_title() added

`PATCH .../artifacts/{aid}` used to inline `db.update("instance_artifacts",
..., {"title": ...[:200]})` in the route handler — a repository bypass with
the truncation rule living in the wrong layer. `update_title()` brings the
write (and the 200-char cap + `updated_at` bump) into the repository, matching
`update_pointer`'s conventions. The business-logic callers now live in
`narranexus/platform/artifact/` (ArtifactService); this repository stays the
single SQL surface for `instance_artifacts`.

## 2026-05-19 — quota helpers removed

`count_for_user()`, `total_bytes_for_user()`, and `total_bytes_for_agent()`
are gone, along with the per-user artifact quota they fed (see
[[artifact_runner.py]] 2026-05-19 note). `idx_artifact_agent_id` stays —
agent-scoped scans still want it.

## 2026-05-14 — pointer model: version table dropped

The repository no longer touches `instance_artifact_versions`. Changes:
- `create()` is now a plain single-row insert — the entity carries `file_path`
  + `size_bytes` (the runner computes both).
- new `update_pointer()` overwrites `file_path` / `size_bytes` / `title` /
  `description` in place — this is the `target_artifact_id` re-registration path.
- `iterate()`, `list_versions()`, `_row_to_version()` removed.
- `delete()` / `bulk_delete()` only remove the artifact row; on-disk source
  cleanup is the route layer's job (gated on `delete_source`).

# Intent

Pure DB I/O for `instance_artifacts`. One row = one artifact = one pointer to an
entry file in the agent's workspace. Business rules (path validation, kind
checks) live upstream in `artifact_runner`; this layer is deliberately dumb.

## Upstream

- `artifact_runner.register_artifact` — the production caller.
- `backend/routes/agents/artifacts.py` + `artifacts/users.py` — list / detail /
  pin / delete endpoints.
- Tests — `tests/repository/test_artifact_repository.py` (real in-memory SQLite).

## Downstream

- `AsyncDatabaseClient` (utils/db/database.py) — CRUD helpers + `execute` for raw SQL.
- `schema_registry` `instance_artifacts` table — row shape.
- `BaseRepository[Artifact]` — `get_by_id`, `get_by_ids`, `find`, `find_one`.

## Design decisions

- **No version table, no transactions.** A single artifact row is one write.
  `create()` / `update_pointer()` / `delete()` are each a single statement, so
  the old two-table atomicity concern is gone.

- `set_pinned` uses raw SQL for both pin and unpin because
  `AsyncDatabaseClient.update()` filters out `None` values, making it impossible
  to explicitly SET a column to NULL via the CRUD helper. On pin: saves current
  `session_id` into `original_session_id` (via `COALESCE` so a re-pin is a no-op
  on that column) and sets `session_id = NULL`. On unpin: restores `session_id`
  from `original_session_id` and clears `original_session_id`.

- `list_by_session()` uses raw SQL because the simple `filters` dict passed to
  `BaseRepository.find()` cannot express `AND pinned = 0` alongside `session_id`.

- Placeholder style is `%s` (MySQL convention). `AsyncDatabaseClient` translates
  to `?` for SQLite via `_mysql_to_sqlite_sql`.

## Gotchas

- `_entity_to_row()` coerces `pinned` to `1`/`0` because SQLite stores booleans
  as INTEGER.

- `_row_to_entity()` calls `_parse_bool()` on `pinned` because SQLite returns
  INTEGER (0/1), not Python `bool`.

- `_row_to_entity()` defaults `file_path` to `""` and `size_bytes` to `0` for
  legacy (pre-pointer-model) rows that never had these columns populated — such
  rows won't render but won't crash the list query either. They are hand-migrated
  per the cleanup TODO.

## 2026-08-18 — `content_hash` 贯通行↔实体转换与 update_pointer

`_row_to_entity`/`_entity_to_row` 是显式枚举字段的(新列默认会被静默丢掉——本次
就踩了:写入成功读回 None),加列必须同步补这两处。`update_pointer` 增
`content_hash` 参数,语义为 as-given 直写(含 None)。

## 2026-08-18(二)— `list_file_paths_for_heal_scope`

heal 护栏的数据源:同 scope(私有=本 agent 无队行;团队=该队全行)活 artifact 的
file_path 集合,候选凡命中即排除。scope 口径刻意镜像 heal 的 search_root。

## 2026-08-18 — `count_for_agent_context`

状态块尾注的 COUNT 版可见面查询(同 union 语义),不为拿总数付整行代价。

## 2026-08-20 — 可见性谓词单一定义 + 过滤下推(#334 I10)

`_AGENT_CONTEXT_WHERE` 模块常量:list/search/count 三个口拼同一段
(参数恒 (agent_id, agent_id))——安全谓词的第二份手抄消灭。新增
search_agent_context / count_agent_context_filtered:list_artifacts
工具的过滤与分页下推进 SQL,LIKE 转义 %/_(字面 "a_b" 不再匹配
"axb");大小写按 DB collation(SQLite 仅 ASCII ci,较旧 .lower()
对非 ASCII 略窄——如实声明)。
