---
code_file: frontend/src/types/teams.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `TeamMemberActivity.last_turn_silent?: boolean`

idle/queued 条目上的可选布尔,后端 `_member_activity` 产出。`TeamChatResponse.patrol_enabled?: boolean` 同日新增。


## 2026-08-17 — `SkillExportSpec` 去掉 archive_path / manual_zip_path

导出请求里不再有路径字段（SEC-07，见 [[bundle.py]]）：客户端选 method，
服务端按 user 查 `skill_archives` 定 bytes。`SkillArchiveRecord.archive_path`
保留——那是服务端下发的、只用来显示 basename 的字段，方向相反。

老客户端发上来的 `archive_path` 是被**忽略**（pydantic 默认
`extra="ignore"`），不是被拒绝。这里**故意不加** `extra="forbid"`：还在用
旧 DMG 的用户前端仍会发这个字段，forbid 会让他们的导出直接 422，而服务端
本来就不再读它——忽略掉严格更优。铁律 #2 反对的是留兼容 shim，不是反对
宽容地忽略一个无关字段。

## 2026-08-14 — TeamChatMessage.is_platform

"这一行是平台在自述吗"，由服务端回答（见 [[teams.py]]）。前端不再维护
`PLATFORM_MSG_TYPES` 的第二份拷贝：线上传的是字符串，一个前端不认识的类型会被渲染成成员
发言——带身份色、头像，名字位置是 `team_<id>`。`msg_type` 仍然说的是**哪一种**，用来选文案。

## 2026-08-14 — TeamWithMembers 带上房间活动

`last_message_at` / `last_message_preview` / `last_message_author`：房间上一次说了
什么、谁说的。这是未读标记的**服务端那一半**——客户端那一半（水位线）在
[[unread.ts]]，逐设备存在 localStorage 里，服务端无从知道，所以它只回答"这个房间
上次说话是什么时候"。

三个字段对以下房间都是 null：还不存在的房间；以及只有用户自己的消息和平台自己的
通知的房间——两者都不算数，否则发一条消息就会给自己发消息的那个房间打上标记。

## 2026-08-10 — TeamWorkItem / TeamWorkBoardResponse

工作板的类型。`status` 在用户侧只会收到 ACTIVE 三态 + `paused`;`msg_type`
的取值增加 `'patrol'`(与 `'system_stop'` 同属房间级系统行)。

## 2026-08-21 — TeamWorkItem 增 `kind` 及交接卡字段

`TeamWorkItem` 现在是两种卡的联合(靠**必填** `kind` 区分,用 discriminated
union 让 TS 收窄):`'task'` 是显式任务(沿用 `title`/`assignee_name`);
`'handoff'` 是一条 @ 消息的 auto 交接单合并后的卡,带 `source_name`(发件人,
可能是队友也可能是用户自己)、`assignee_names`(仍欠回复的人)、`item_ids`
(底层多行),**不带 title**(消息正文是发件人的话,上板会被误读成收件人说的)。

`status` 在 handoff 上是**聚合值**,可能是 `in_progress` 而 `paused_item_ids`
仍非空(一次 resume 只成功了一半)。`paused_item_ids` 是独立维度、不塞进
`status`,前端据它决定是否给 resume 按钮、并只对这些行发恢复请求。渲染与恢复
逻辑见 [[TeamWorkBoard]]。

## 2026-08-07 — TeamChatMessage.msg_type

`'text' | 'multimodal' | 'system_stop'`。见 [[TeamChatPanel]] 的系统行分支。

## 2026-07-31 — TeamChatMessage.event_id

`TeamChatMessage` gained `event_id?: string | null` — the turn that produced
an agent reply (null for user messages / legacy rows). Consumed by
[[TeamMessageProcess]] for the per-message reasoning disclosure.

## 2026-07-30 — TeamMemberActivity.event_id

The activity payload now carries the `events` row id of the member's
current/most recent turn (written server-side by `TurnActivity.note_event_id`).
The roster's expanded detail uses it to fetch the finished turn's full
event_log through the existing event-log endpoint — the missing link that
lets a team room show single-agent-grade process detail without a new route.

## 2026-07-28 — activity gains states, steps and the lead

`TeamMemberActivity.status` is now `running | stalled | queued | idle`
(`TeamMemberStatus`), plus `last_signal_at` / `finished_at` /
`queued_count` / `queued_since` / `steps`. `TeamActivityStep` +
`TeamActivitySteps` describe the per-turn phase timeline. `thinking` is removed
from `TeamChatHistoryResponse`; `lead_agent_id` is added. See [[teams]].


## 2026-07-20 — TeamChatMessage.attachments

`TeamChatMessage` gained `attachments?: BusAttachment[]` so team-chat bubbles can
render files sent/shared into the room (see [[BusAttachmentList]]).

## 2026-07-13 — skill-secret bundle types

`BundleExportRequest.include_skill_secrets` and `BundleManifest.contains_skill_secrets`.

## 2026-07-13 — bundle credential types

`BundleExportRequest.include_channel_credentials`, `BundlePreflightResponse.credential_clashes`, `BundleManifest.contains_channel_credentials`, and confirm counters `channel_credentials_imported` / `channel_credentials_skipped_conflict`.

# teams.ts — Frontend types for teams (incl. team group chat) + bundle export/import

## Why it exists

Mirrors the backend's Team / TeamMember / TeamChat / Bundle request/response
shapes into TypeScript interfaces so the frontend stays field-for-field aligned
with the Pydantic models. When adding a type, change the backend Pydantic first,
then mirror it here — otherwise runtime field-name drift bites silently.

## How it works / design

- **Team group chat is the new core.** A team is now a group chat over the
  message bus, so the file carries the chat wire types: `TeamChatMessage`
  (`from_agent` is `usr_<user_id>` for the human, else an agent_id; `is_user`
  disambiguates rendering), `TeamChatHistoryResponse` (messages + a `thinking[]`
  of member agent_ids the trigger is mid-processing → the "…" indicator), and
  `TeamChatSendResponse`. These back `api.getTeamChat` / `api.sendTeamChat`
  ([[api]]). `@mention` delivery is expressed on the send side, not in these
  shapes — see `sendTeamChat`'s `mentions` arg.
- **Team CRUD types**: `Team`, `TeamWithMembers`, `TeamListResponse`,
  `TeamOperationResponse` back [[TeamManagementModal]] / [[teamsStore]].
  `intro_md` doubles as the bundle's default README.
- **Bundle export/import types** (subproject 2) live here too: `BundleExportRequest`
  with its many per-agent opt-in allowlists (`mcp_selection` opt-in by design,
  `narrative_/event_/job_/artifact_selection` null = include all),
  `BundleManifest` / `BundlePreflightResponse` / `BundleConfirmResponse`, plus the
  wizard preview types and `SkillArchiveRecord`.
- **Gotcha**: same skill name can map to N agents (`SkillExportSpec.agent_id` +
  `skill_dir` disambiguate the physical folder); `skill_name` from frontmatter is
  NOT filesystem-unique.

## 2026-07-21 — Team.lead_agent_id

`Team` gained `lead_agent_id?` (default responder; null = earliest member). Set via the
TeamManagementModal picker → `updateTeam`. See backend [[teams]].

## 2026-08-11 — `BulletinEntry` / `TeamBulletin`

`source` 驱动权限与渲染，`author_id` 只驱动「由谁添加」标签、自动总结为 null。
`TeamBulletin` 把 usage/limits 一起带上，见 [[api]]。

## 2026-08-12 — `TeamChatMessage.segments`

可选。缺失表示「没有记录边界」——本改动之前写入的每一条消息、以及任何没有独白的路径。
气泡把这类消息按整块渲染，也就是它此前的样子。**不回填、不猜**（铁律 #2）。
