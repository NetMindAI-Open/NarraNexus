---
code_file: backend/routes/teams.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — PR #141 review hardening (attachments + wipe + layering)

Three changes from review:

- **Echoed attachment dicts are no longer trusted.** ``send_team_chat`` used
  to store the client's whole dict after only validating ``rel_path`` — an
  open client-writable JSON channel into ``bus_messages.attachments`` and
  (via ``transcript`` in ``build_bus_markers``) raw text into the team
  prompt. Now ``_sanitized_attachment`` uses the echoed ``rel_path`` ONLY to
  locate the file, then reloads the dict the upload endpoint persisted
  server-side (``store_bus_attachment_meta`` → ``load_bus_attachment_meta``
  sidecar, see [[_bus_attachment_impl]]); no sidecar → minimal metadata
  rebuilt from disk, never a client transcript.
- **MIME sniffing consolidated.** The local ``_sniff_upload_mime`` (which
  returned libmagic's octet-stream verdict directly, diverging from the
  other two copies) is gone; the upload endpoint calls the shared
  [[mime_sniff]] helper. ``store_bytes_into_bus`` is now awaited (its disk
  write moved off the event loop).
- **Wipe without N+1.** ``_wipe_team_data`` deletes ``bus_message_failures``
  with one IN-subquery statement (bare identifiers, dialect-portable) instead
  of pulling every message_id into memory and deleting row-by-row inside the
  open transaction.
- Imports go through the public facades ``message_bus.attachments`` /
  ``message_bus.activity`` instead of the private impl modules.

## 2026-07-20 — team-chat messages carry attachments

`get_team_chat` now includes `attachments` (from `BusMessage.attachments`) per message, so
files an agent sent/shared into the room render in the group chat. Bytes are served by the
shared endpoint `GET /api/agent-inbox/attachments/raw?path=<rel_path>` (see [[inbox]]);
teams.py doesn't add its own serving route.

## 2026-07-22 — clear team data (counterpart to agent wipe)

New `DELETE /api/teams/{team_id}/data?chat=&files=` (owner-only) + `_wipe_team_data`. The
team analog of `wipe_agent_data`: clears the collaboration *surface* but KEEPS the team,
members, and the bus channel + membership. `chat` → delete `bus_messages` (+ their
`bus_message_failures`) for the team room channel (`created_by='team_<id>'`); `files` →
`shutil.rmtree` the `_shared/teams/{team_id}` dir. DB deletes in a transaction (commit
first), disk delete best-effort after. Idempotent (no room / no dir → zeros, no error).

## 2026-07-21 — default responder (no-@mention messages)

A team message with NO @mention used to trigger nobody (team rooms have a non-agent
`created_by`, so no member is the always-activated owner → silence). Now `send_team_chat`
routes an un-addressed message to exactly one agent via `_resolve_default_responder(team,
members)` = `team.lead_agent_id` if it's a current member, else the earliest-joined member
(`list_members_by_team` is ordered by `joined_at`). A single-agent team therefore
auto-responds; the picked agent can @-delegate. `update_team` (PATCH) sets/validates the
lead — a non-empty value must be a member; `""` clears it (exclude_none drops null, so empty
string is the "clear" wire signal). New nullable `teams.lead_agent_id` column.

## 2026-07-21 — voice input (parity with single-agent chat)

The upload endpoint gained a `source` query param and, for `audio/*` uploads, runs
`TranscriptionService` (Whisper) — same as the single-agent path — so @mentioned agents
receive the spoken words via the attachment marker (they can't listen). `agent_id=""` is
passed to `transcribe` because a team memo has no single agent; the NetMind signed-URL path
resolves the file via the shared-area fallback in [[transcription_public]]. `transcript` /
`source` land on the bus-attachment dict; the response echoes `transcription_available` so
the composer can show a "voice unavailable" notice. OpenAI-backend transcription reads the
local shared file directly (no fallback needed).

## 2026-07-21 — USER can attach files to a team message

New `POST /{team_id}/chat/attachments` (multipart) stores a user upload into the sender's
shared bus area via `store_bytes_into_bus` ([[_bus_attachment_impl]]) after team-ownership
check + server-side MIME sniff (`_sniff_upload_mime`, libmagic→ext→client) + size cap
(`backend.config.settings.max_upload_bytes`), returning a bus-attachment dict.
`TeamChatSendRequest` gained `attachments: list[dict]`; `send_team_chat` re-validates each
via `resolve_shared_file_for_user` (reject tampered rel_path), allows an attachment-only
message (empty content OK when files present), and passes them to `bus.send_message`. So a
human upload flows the same path as an agent-attached file (same shared area, same Read).

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

## 2026-07-22 — get_team_chat returns per-member activity

`get_team_chat` now returns `activity: [{agent_id, status, phase?, tool_count?, started_at?}]`
alongside `thinking` (kept for back-compat). status = running (from [[_bus_activity]]
`is_live`, with live phase + elapsed) / queued (pending @mention, not yet running) / idle.
Drives the team status strip + activity bubbles.
