---
code_file: src/xyz_agent_context/schema/team_schema.py
last_verified: 2026-07-21
stub: false
---

# team_schema.py — Pydantic 模型 for subproject 1

定义 `Team`, `TeamMember`, plus API request/response 模型 (`CreateTeamRequest`, `UpdateTeamRequest`, `TeamWithMembers`, ...)。

`Team.source` 字段语义：
- `"user"` — 用户在 UI 创建
- `"bundle"` — 从 .nxbundle import 进来（bundle.importer 设的）

`Team.intro_md` 字段是议题 8 的 onboarding（README.md）落地点。

## 2026-07-21 — Team.lead_agent_id

`Team` + `UpdateTeamRequest` gained `lead_agent_id` — the default responder for a team-chat
message with no @mention (None = earliest-joined member). See [[teams]].
