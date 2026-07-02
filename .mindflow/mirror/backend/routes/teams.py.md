---
code_file: backend/routes/teams.py
last_verified: 2026-06-23
stub: false
---

## 2026-06-23 — team group chat（基于 message bus，无 schema 迁移）

新增两个端点，把"团队群聊"叠在现有 message bus 上：
- `POST /:id/chat/messages`：用户以合成发送者 `usr_<user_id>` 发言，
  `mentions` 带 agent_ids（UI 的 `"@all"` → bus `"@everyone"`）。
- `GET  /:id/chat/messages`：返回转录（`usr_…` 解析为用户名）+
  `thinking`（在本房间有未处理 @ 的成员 → 前端 "…" 输入指示）。

`_get_or_create_team_room` 把团队映射到一个 group channel,并把
`created_by` 改写成非 agent 标记 `team_<team_id>`:既能确定性地找到房间
(不加列),又保证没有"房主 agent"被 MessageBusTrigger 无条件唤醒——
投递纯靠 @。成员每次同步到团队当前 agents。回复由独立的
MessageBusTrigger 在服务端产生(见 `message_bus_trigger.py.md` 的 team
分支),前端只轮询这两个路由。`TEAM_ROOM_OWNER_PREFIX` /
`USER_SENDER_PREFIX` 与 trigger 保持同步。

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
