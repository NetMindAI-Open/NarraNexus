---
code_file: src/xyz_agent_context/repository/team_repository.py
last_verified: 2026-05-08
stub: false
---

# team_repository.py — Team & TeamMember CRUD (subproject 1)

## 为什么存在

子项目 1 (Team Membership + Sidebar) 要求一个用户可以创建多个 team，每个 team 可以有多个 agent，一个 agent 可以在多个 team。两张表 `teams` + `team_members`。本仓库提供 CRUD + 一个**反向查 team-mates** 的核心查询：`list_team_mates(agent_id)` 是 social network derived join 的数据源。

## 上下游关系

- **被谁用**：
  - `backend/routes/teams.py` — REST CRUD
  - `module/social_network_module/social_network_module.py` — `hook_data_gathering` 里 derive 同 team agent
  - `bundle/importer.py` — import 时建 team + 加 member
- **依赖谁**：`utils/database.AsyncDatabaseClient`、`repository/base.BaseRepository`

## 设计决策

### `list_team_mates` 自反 join

不在 SQL 一次查（避免 cross-database 写法差异），分两次：(1) 查 caller agent 在哪些 team；(2) 对每个 team 查所有成员。`exclude_self=True` 时移除 caller 自己。

### `team_members` 没有 `agent_owner_user_id` 列

权限校验放在 routes 层（`team.owner_user_id == request user_id`）。table_id_field = `id` 用 surrogate PK，业务键是 `(team_id, agent_id)` 复合。

### `gen_team_id` 用 `secrets.token_hex(6)`

12-hex 字符串足够避免碰撞。前缀 `team_`。

## Gotcha

- `delete_team` 只删 `teams` 表行；调用方（routes/teams.py）必须**先**调 `member_repo.remove_all_members(team_id)`。这条契约只在 routes 层保证。
