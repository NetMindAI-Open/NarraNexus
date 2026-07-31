---
code_file: frontend/src/components/chat/team/TeamChatPanel.tsx
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — roster v2 接线：accent 下传 + drawer 不再定宽

TeamRosterPanel 拿到 `accent`（团队色）画选中态；移动端 drawer 的
className 去掉 w-64 —— 宽度归 roster 自己（256↔430px 呼吸），定宽会
把展开变宽顶掉（cn 是 tailwind-merge，后写的宽度赢）。

## 2026-07-31 — 每条 agent 回复挂自己的 reasoning 展开条

气泡内 `BusAttachmentList` 之后渲染 [[TeamMessageProcess]]（仅
`!is_user && m.event_id`）——单聊 MessageBubble "View reasoning & tools"
的团队版。数据链：trigger 发帖时把 turn 的 event_id 写进
`bus_messages.event_id` → chat GET 序列化 → 气泡按需 `getEventLog`。
历史消息 event_id 为 null，自然不显示按钮（无死按钮）。分工：**消息气泡
负责历史（每条 turn 各自可开）**，roster 详情只负责 live/最近一轮。

## 2026-07-30 — guide 横幅退役：空房 hero + member bar 的 `?` popover

`TeamRoomGuide` 那条常驻灰字横幅整个删掉（文件已删），寻址规则改由
[[TeamRoomHero]] 提供的两个出口承载：空房时 `messages.length === 0` 分支渲染
hero（替掉原来的 `Users2 + chat.team.empty` 块，`chat.team.empty|emptyHint|
emptyHintWithLead` 三个 key 就此在代码里无人读），有消息之后靠 member bar 里
`?` 按钮弹出的 `GuideRuleCards` popover。横幅是「提示债」——为一个用户只需要
知道两次的事实永久占掉一条房间宽度，还得靠 localStorage 记折叠状态才勉强能忍。
新方案两处都是按需出现，`nx.team.guide.*` 记忆随之消失，无需迁移。

`?` 容器接管了原先 Settings2 身上的 `ml-auto`（Settings2 去掉，否则两个
`ml-auto` 会把设置钮推到自己一组里）。popover 靠 `guideRef` + document 上的
`mousedown` 监听外点关闭，且只在 `guideOpen` 为真时挂监听——常挂会让每次点击
都过一遍这段判断。

## 2026-07-30 — 两栏布局：roster 常驻，console/气泡退役

Timeline+Composer 外包一层两栏容器，右侧挂常驻 [[TeamRosterPanel]]（桌面
恒显，窄屏由 member bar 的 Users2 钮开 overlay drawer）。据此退役两个旧
表面：顶部折叠 console（TeamActivityConsole 文件已删）和消息流底部的
TeamActivityBubble——后者的 idle-trace 保留期正是「跑完后气泡赖着不走」
的根源。消息流只剩本文件内的 TypingIndicator：仅 running 成员渲染、无
统计、回复落地即消失；点击它展开 roster 里该成员的详情（expandedId 状态
由本组件持有，两侧共享同一高亮，这就是 roster 用受控 props 的原因）。


## 2026-07-28 — moved into `chat/team/`, activity + guidance split out

The surface reached three files, so it became a package (铁律 #23):
[[TeamActivityConsole]] (status console + transcript bubble) and
`TeamRoomGuide` (addressing help, retired 2026-07-30) moved out of this file; the panel now
composes them. `@/components/chat/TeamChatPanel` → `@/components/chat/team`.

What the panel itself kept:

- **One clock.** A single `now` (1s ticker, epoch ms) is passed to every child
  so no two durations on screen disagree by a tick. The ticker now runs while
  ANY member is non-idle, not only `running` — the `queued` "waiting Nm" and
  `stalled` "silent Nm" counters are the whole point of those states.
- **`lead_agent_id`** from the response drives both the guide's "who answers"
  line and a dot on that member's avatar in the roster.
- `thinking` is gone; the empty state and the bubble list derive from
  `activity` (see [[teams]]).

The old inline status strip and the dumb "…" queued bubble are replaced by the
console and [[TeamActivityConsole]]'s bubble respectively.


## 2026-07-21 — voice input (mic), parity with single-agent chat

Tools row gained an `AudioRecorder` (mic) next to the attach `+`. Records → uploads with
`source:'recording'` → backend Whisper → the memo joins `pending` and renders as a
`VoiceTranscript` chip; on send it flows like any bus attachment (agents get the transcript
in their marker). Reuses the ChatPanel plumbing: a mount `getTranscriptionAvailability`
probe, a click-time `onPreflight` re-check, a "voice unavailable" `<Dialog>` (→ Settings),
and a post-record notice banner. New i18n keys `chat.team.transcriptionUnavailable|
voiceUnavailableTitle|voiceUnavailableBody|voiceUnavailableProbeFailed|openSettings|cancel`.

## 2026-07-21 — user can attach files in the composer

Composer gained a paperclip button + hidden multi `<input type=file>`. Picked files upload
immediately via `api.uploadTeamChatAttachment` into a `pending: BusAttachment[]` state,
shown as removable chips above the textarea; `handleSend` passes `pending` to
`api.sendTeamChat` and clears it (restores on failure). An attachment-only message (no text)
is allowed. New i18n keys `chat.team.attach|uploading|removeAttachment` (en+zh).

## 2026-07-20 — bus attachments render in the room

Each message bubble now renders `<BusAttachmentList attachments={m.attachments} />`
below the text, so files an agent sent/shared into the team room show as
chips/thumbnails. `TeamChatMessage` gained `attachments?: BusAttachment[]`
(populated by `GET /api/teams/{id}/chat/messages`). See [[BusAttachmentList]].

# chat/team/TeamChatPanel.tsx — Team group-chat surface

## Why it exists

The user-facing view of the homepage's "agent team": one team's shared room,
rendered in the SAME main slot as the single-agent [[ChatPanel]] so switching
between an agent and a team is seamless (see [[MainLayout]]'s `TeamChatView`).

## How it works

Messages flow over the **message bus**, NOT a single-agent narrative:
- **Send** → `api.sendTeamChat(teamId, content, mentions)` → `POST
  /api/teams/{id}/chat/messages`. The composer text's `@tokens` are resolved to
  member `agent_id`s (or the literal `"@all"`); the backend posts as the
  synthetic sender `usr_<user_id>` and maps `@all` → bus `"@everyone"`.
- **Transcript** → polls `GET /api/teams/{id}/chat/messages` every `POLL_MS`
  (3s). The response also carries `thinking: string[]` — members the bus trigger
  is currently processing — which drives the "…" typing bubbles.

## Design decisions / gotchas

- **@-mention autocomplete** with an `@all` option pinned on top; keyboard
  (↑↓/Enter/Tab/Esc) + click. `@all` is a `MentionOption` kind, not a member.
- Bubbles mirror the single-agent [[MessageBubble]]: carbon-soft (user, right,
  carbon right-edge) vs silicon-soft (agent, left, silicon left-edge), meta row
  outside the bubble. Agent content is rendered through `<Markdown>` (the replies
  are markdown); user content stays plain text. `.content.trim()` + the global
  `.markdown-content > :first-child/:last-child { margin: 0 }` rule keep the
  agent bubble's vertical padding equal to the user bubble's.
- The room itself is created/owned by the backend (`created_by = team_<id>`);
  this panel never touches the bus directly — it only calls the two team-chat
  routes. Agent replies (and agent→agent @ cascades) are produced server-side by
  the MessageBusTrigger and just appear in the polled transcript.

## 2026-07-22 — team activity visualization

Consumes the new `activity` from `getTeamChat` ([[teams]]). Renders a top **status strip**
(chip per running/queued member: dot + name + phase·elapsed) and, at the bottom of the
timeline, an **activity bubble** per active member — running shows a spinner + live phase
(思考中 / 调用 <tool> / 回复中) + elapsed; queued shows the "…" dots. A 1s ticker advances
elapsed between the 3s polls. Replaces the old dumb `thinking` "…" bubbles. i18n
`chat.team.activity.*`.
