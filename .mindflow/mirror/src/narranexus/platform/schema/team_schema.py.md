---
code_file: src/narranexus/platform/schema/team_schema.py
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — Team.source 增加 "agent" 取值

docstring 的 source 取值说明加一行:`"agent"` = agent 经 `create_team` MCP 工具建的 team(区别于 UI 的 `"user"` 与 bundle 导入)。纯文档;`create_team` 建 team 时传 `source="agent"`。无按 team source 分支的消费逻辑(核过 `source==` 只在 provider 侧,与 team 无关)。

## 2026-08-14 — TeamWithMembers 增加房间活动三字段

`last_message_at` / `last_message_preview` / `last_message_author`。这是
sidebar 未读标记的服务端那一半，由 [[teams.py]] 的 `_team_room_activity` 填充；
客户端那一半（水位线，逐设备）在前端。

放在响应模型上而不是 `Team` 上是有意的：这不是 team 的属性，是**这次列举时**房间
的状态。写进 `Team` 会让它看起来像一列可持久化的字段，然后迟早有人试图去更新它。

## 2026-08-10 — Team 增加 patrol_enabled / last_patrol_at(只读)

Leader 巡查的两列。**`patrol_enabled` 为 NULL 时,对*有 lead* 的 team 读作
开** —— 设 lead 这个动作本身就是在说「这个负责」,不必再问一遍;没有 lead 的
team 永不巡查,平台不替用户指定负责人。这条规则不显然,而它此前只活在代码注释里。

两列都**不写进 `_entity_to_row`**:归 [[patrol]] 与巡查开关端点所有。走 team
CRUD 的话,一次无关的改名就会把巡查游标清掉。

# team_schema.py — Pydantic 模型 for subproject 1

定义 `Team`, `TeamMember`, plus API request/response 模型 (`CreateTeamRequest`, `UpdateTeamRequest`, `TeamWithMembers`, ...)。

`Team.source` 字段语义：
- `"user"` — 用户在 UI 创建
- `"bundle"` — 从 .nxbundle import 进来（bundle.importer 设的）

`Team.intro_md` 字段是议题 8 的 onboarding（README.md）落地点。

## 2026-07-21 — Team.lead_agent_id

`Team` + `UpdateTeamRequest` gained `lead_agent_id` — the default responder for a team-chat
message with no @mention (None = earliest-joined member). See [[teams]].

## 2026-08-11 — 公告栏模型与预算常量

`BulletinEntry` / `BulletinUsage` + 6 个常量。预算常量放在 schema 层，
是因为**四个**地方要读同一组数字：REST 路由、MCP 工具、bundle 导入、前端 limits 响应。

`BULLETIN_MAX_SUMMARY_CHARS` 与条目预算**分开**是刻意的：自动产出永远不该有能力
把用户亲手写的规矩挤出 prompt。
## 2026-08-10 — `patrol_is_on` 搬到这里

规则读的两个字段(`lead_agent_id` / `patrol_enabled`)都是 Team 的字段,所以规则
归 Team。它此前住在 `team_work_schema.py`,只因为巡查是当时唯一的调用方 —— 结果
是一条 Team 规则藏在工作项模块里,看 Team 的人找不到。

规则本身容易说、也容易说错:`patrol_enabled` 对所有早于该列的 team 是 NULL,
NULL 读作**开**;但仅限**有 lead** 的 team —— 设 lead 才是「指定了负责人」这个
动作,没有 lead 的 team 永不巡查,平台不替用户指派。

## 2026-08-11 (review 收口) — `resolve_default_responder` 与房间前缀迁入

规则本身没变，只是从 `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py` 搬到了 Team 规则该在的地方——理由和
`patrol_is_on` 一个 release 前的搬家完全相同：**它是一条关于 Team 的规则，却待在一个
核心包够不着的模块里**，于是总结 worker 长出了第二份拷贝。一条规则两份实现，就是会漂移的那种。

`TEAM_ROOM_OWNER_PREFIX` / `USER_SENDER_PREFIX` 同理：四个模块在构造或匹配这两个合成标记，
此前各自重打字面量。

## 2026-08-11 (review 收口 2) — 前缀真的只剩一处定义了

上一轮我写的注释是「四个模块都已统一」，**一次 grep 就证伪**：`team_summary_worker`、
`backend/routes/runs`、`_work_board_mcp_tools` 三处仍在各自定义同一个字面量，其中第一个
正是注释点名的那个。**一条被证伪的注释比没有注释更贵。**

三处都改为 import，现在 `TEAM_ROOM_OWNER_PREFIX` / `USER_SENDER_PREFIX` 在全仓只有这一处定义。
`message_bus_trigger` 里那两段「Keep in sync with plugins/builtin.teams/src/narranexus_plugins/teams/routes.py」的注释也删了——
定义搬走后它们悬在原地，而且要求读者去做的正是这次改动要消灭的事。其中「为什么必须是
non-agent marker」那段理由有价值，搬到了定义处而不是丢掉。

## 2026-08-11 (review 收口 3) — `resolve_default_responder` 收 lead id，不收 Team

原来它在函数里嗅探拿到的是 model 还是 dict（一个调用方持模型、另一个持原始行）。
**那是把一次类型检查塞进了一条与类型无关的规则里。** 现在直接收 `lead_agent_id`，
由调用方说明自己手上是什么。
