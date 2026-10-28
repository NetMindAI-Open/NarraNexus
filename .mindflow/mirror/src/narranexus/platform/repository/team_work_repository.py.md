---
code_file: src/narranexus/platform/repository/team_work_repository.py
last_verified: 2026-08-14
stub: false
---

## 2026-08-14 — 差事层的两个读 + `origin` 写入

`create_item` 增 `origin`（默认 `tool`，见 [[team_work_schema]]）。

`has_errand_for(source_message_id, assignee_id)`：去重键是**消息**而不是
(负责人, 标题)——轮询会重投、重试的帖子保持同一个 id，而同一件事真的第二次交接
是第二个差事，必须允许。**任何状态都算**，含终态：重开一个刚交付完的项会让负责
人永久迟到。

`list_open_errands(channel_id, assignee_id)`：两个收敛范围都是刻意的。按
**房间**是因为一个 agent 属于多个团队，在这屋说话不能结清那屋欠的账；按
**origin** 是因为 `tool` 行是任务，归 Leader 关。

索引同批加两条（`(channel_id, assignee_id)` 与 `source_message_id`），它们是差事
层仅有的两个热读。

# team_work_repository.py — 工作板的数据访问

## 为什么在 repository/ 而不在 module 里

项目约定 repository 一律放 `repository/`;更实际的原因是**它有两个不属于
module 的消费者**:`MessageBusTrigger` 的巡查候选查询,和取消端点的
停止→暂停联动(`backend/routes/runs.py`)。放进 module 会让这两处反向依赖一个
module 的私有实现。

## 设计决策

- **`list_active` / `teams_with_active_work` 是巡查的成本护栏**。「板子空了就
  一个 run 都不产生」这条保证,落在这两个查询排除 `paused` 与终态上。
  `teams_with_active_work` 一条查询覆盖整个 fleet —— 与 `_agents_with_pending`
  同一形状、同一理由:否则每轮要问每个 team 一遍才知道它没事干。
- **`pause_by_root("")` 是 no-op,不是 match-all**。前置列时期的工作项
  `root_run_id` 为 NULL,把空值当「同一棵树」会让一次停止冻结整块板子。
  测试专门钉了这条。
- **未知 id 返回 False/None,不抛**。item_id 来自模型写的工具调用,一个拼写
  错误必须读作「没找到」,而不是变成 agent 转述给用户的「平台故障」。
- `pause_by_root` 逐行写并吞掉单行失败:部分暂停的板子仍然好过一块继续复活
  工作的板子。

## 上下游

- **上游**:[[_work_board_mcp_tools]](agent 侧)、`backend/routes/runs.py`
  (停止→暂停)、未来的巡查候选查询
- **数据**:`team_work_items`,见 [[schema_registry]]

## 2026-08-10 — `list_visible`: why the user's board is a repository query

The board endpoint originally hand-wrote its own `SELECT` in `routes/teams.py`,
because it needs one state more than `list_active` does (`paused`). That split
the feature's SQL across two layers, and the project's rule that new raw SQL
owes a real-MySQL test then applied to a statement sitting in a route — where
nobody would think to look for it.

So the variant lives here instead. The pair reads as one decision:

  * `list_active` — the AGENT's board. Hides `paused`, which is what makes a
    stop actually stop: patrol asks this question, and a parked item must not
    read as "unfinished, go chase it".
  * `list_visible` — the USER's board. Shows `paused`, because deciding whether
    to resume is the user's call. If the UI reused `list_active`, pressing stop
    would erase the task from the board and a stop would be indistinguishable
    from a delete — exactly what the pause-not-cancel decision exists to avoid.

Both are covered against a real MySQL (`test_team_work_repository_mysql.py`):
they share the generated-placeholder `IN (...)` shape, which is the shape that
has produced a 1064 in this codebase before.

## 2026-08-10 — `_list_by_status`:方言面收敛成一条,顺带修掉排序不确定

`list_active` / `list_visible` 只差一个状态,却各自拼一遍 `IN (%s, ...)`。代价
不是「不好看」:**每复制一次语句形状,真 MySQL 套件就得多一个用例**,因为生成
式占位符正是这个仓库出过 1064 的那个形状。两个方法现在各剩一行,语句只有一份,
MySQL 用例覆盖的是**唯一**那份拼装,而不是「碰巧两份长得一样」。

排序同时从 `ORDER BY created_at ASC` 改成 `created_at ASC, id ASC`。
`schema_registry` 给 `created_at` 在 SQLite 是**秒**精度(MySQL 是
`DATETIME(6)`),所以同一秒创建的两个条目在 SQLite 上时间戳完全相同,顺序退化
成引擎的实现细节 —— 而 SQLite 就是桌面版的生产后端,用户板子上的条目顺序因此
是不确定的。`id` 是自增主键,语义上正好是插入顺序,也就是「oldest first」本来
的意思。
