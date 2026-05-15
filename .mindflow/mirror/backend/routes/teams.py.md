---
code_file: backend/routes/teams.py
last_verified: 2026-05-13
stub: false
---

## 2026-05-13 — local 多用户隔离修复

`_user_id_for_request` 改成走统一 helper
`backend.auth.resolve_current_user_id`——cloud / local 共享同一条
路径，差异在 middleware 内消化。之前 local 模式 fallback 到
singleton "first user" 导致所有 local 用户 owner 相同、teams 互相
可见。详见 `auth.py.md`。

# teams.py — REST routes for team membership (subproject 1)

`/api/teams` CRUD + `/api/teams/:id/members` add/remove。

## 为什么存在

把 `TeamRepository` / `TeamMemberRepository` 暴露给前端 `TeamManagementModal` + `TeamFilterBar` + bundle export wizard。

## 设计决策

### 权限模型

每个端点都用 `_user_id_for_request(request)` 拿 user_id（local 走 `get_local_user_id`，cloud 走 `request.state.user_id`）。所有 team 操作必须 `team.owner_user_id == request_user_id`，跨用户操作返回 403。

`POST /:id/members` 还要校验 `agent.created_by == request_user_id`（不能把别人的 agent 加进自己 team）。

### 删除 team 不删 agents

`DELETE /:id` 只 cascade 删 `team_members` 行，agents 本身保留。

## Gotcha

- 没做 `is_public` 维度（公开 team 给别用户加自己 agent），v1 不做。
