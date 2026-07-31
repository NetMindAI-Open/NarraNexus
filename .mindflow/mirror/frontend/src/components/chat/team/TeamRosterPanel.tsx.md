---
code_file: frontend/src/components/chat/team/TeamRosterPanel.tsx
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — v2 质感重做（Owner 反馈「廉价、没有质感」）

- **列会呼吸**：常态 w-64，成员展开时 `transition-[width]` 到
  `min(430px,92vw)`（terminal 要排面），transcript（flex-1 min-w-0）
  自动让位；motion-reduce 免动画。
- **行有身份**：RingAvatar + AvatarWithStatus 角标（running=绿 /
  queued=amber / stalled=error / idle=灰）、可读的状态词（StatusLine，
  running 时 `$ tool`）替换 1.5px 色点、lead 徽从 2px 隐形点改为
  avatar 左上 accent 圆徽、chevron affordance（hover 显现/展开旋转）、
  选中态 = accent 内嵌竖线 + silicon wash（与 transcript 打字气泡
  高亮同一语言，accent 由 TeamChatPanel 传团队色）。
- **头条带活信息**：working 数 + LiveDot 呼吸灯。
- **详情换心脏**：MemberDetail/PhaseTimeline/CurrentAction 移除，
  换 [[TeamMemberPanel]]（迷你 ProcessPanel：live 走
  useRunObservation 真流，idle 保留 TurnTimeline）。
- 沿袭不变：members 驱动行、activity 只装饰；expandedId 受控单选；
  stalled 行 amber wash；RowMetric 语义。

## 2026-07-31 — detail renders TurnTimeline; fetch hook extracted; ago-only metric

Three changes from the 2026-07-31 user feedback round:

- **Idle detail now renders [[TurnTimeline]]** (the single-chat "view
  reasoning & tools" renderer: THINKING header + Markdown body, expandable
  tool args) instead of `ProcessEventRows`' compact one-line rail. The
  "Depends on processShared for ProcessEventRows" note below is history —
  only `friendlyToolName` remains from there.
- **`useMemberTurnDetail` moved out** to [[useTurnDetail]] (verbatim, renamed
  `useTurnDetail`) because the transcript's per-message disclosure
  ([[TeamMessageProcess]]) needs the identical fetch/cache/race behaviour.
- **`RowMetric` handles `durationMs: null`** (legacy rows without started_at)
  via `chat.team.roster.lastRunAgoOnly` — "finished Nm ago" instead of a
  fabricated "ran 0s". See [[teamActivity]].

# team/TeamRosterPanel.tsx — the room's standing member column

## Why it exists

[[TeamActivityConsole]] answered "is anything happening" — but only after the
user unfolded it, only for members the poll considered active, and only in a
strip that shares the vertical budget with the transcript. The question the
Owner actually holds while a team works is **"what is EACH of them doing, right
now"**, and that question does not survive a fold: a console that is closed by
default is a console that is closed during the 25 minutes it mattered.

So the roster is permanent chrome down the right edge instead. Every member gets
a row, always — including a member the activity payload never mentioned, which
is synthesised as `{ status: 'idle' }` rather than dropped. "Who is in this room"
must not depend on who happens to be busy, and a member vanishing from the list
because it went quiet is worse than a row that says "no runs yet".

It is a separate file rather than a section of [[TeamChatPanel]] because the
narrow-screen layout reuses the identical rows inside a drawer — hence the
`className` shell override — and because a component this dense deserves its own
test file.

## This file does not do

- **It does not own the selection.** `expandedId` / `onToggle` are props: the
  parent needs the same id to drive the transcript's typing bubble for that
  member, and two components owning one selection is exactly how the old
  console and bubble drifted. Same-id → collapse is the parent's decision too.
- **It does not own a clock.** `now` arrives from the panel's single 1s ticker;
  reading `Date.now()` per row would make two rows disagree by a tick.
- **It does not fetch a running member's process.** See below.

## Upstream / downstream

- **Used by**: [[TeamChatPanel]] (next task wires it in — this commit ships the
  component + tests only), which already polls `getTeamChat` for `activity` and
  `lead_agent_id` and holds the `now` ticker.
- **Depends on**: [[teamActivity]] for the whole vocabulary (`compareActivity`
  ordering, `STATUS_TONES`, `phaseLabelKey`, `buildTimeline`, `formatDuration`,
  `lastRunSummary`) — the roster must not re-decide what "stalled" looks like;
  `processShared` for `ProcessEventRows` + `friendlyToolName`, so a member's
  process reads identically to the single-agent ProcessPanel's terminal;
  `segmentTurn`'s `timelineToEvents` to normalise the persisted timeline; and
  `api.getEventLog`.

## Design decisions

- **Two data sources for detail, split by liveness.** A running/stalled member
  expands to the phase timeline the activity poll already carries; an idle one
  expands to its persisted event log. This is not a rendering preference: the
  `events.event_log` row is written at the END of the turn (Step 4), so there is
  literally nothing to fetch while the turn runs. Trying to unify them would
  mean either an empty panel for the live case or a polling read of a row that
  does not exist yet.
- **Fetch keyed by `agent:event`, once per turn.** The room re-renders every
  second and re-polls every three; a request per render is a request storm on a
  detail that cannot change once written. The key is also the race arbiter — a
  response is applied only if its turn is still the current one, so a member
  that starts a new turn while its old detail is in flight never gets the old
  turn's process painted under the new one's header.
- **"Loading" is not a stored state.** It is the absence of a settled result for
  the current key. Storing it would mean a synchronous `setState` inside the
  effect, which this repo's eslint config rejects (cascading renders) — and the
  derived form is strictly better anyway: a stale settled result for a previous
  key automatically reads as "loading the new one".
- **Rejected: an unmount-scoped `alive` flag** for in-flight discard. Collapsing
  a row mid-flight sets `alive = false`, the response is dropped, and
  re-expanding hits the "already requested this key" cache — the row is then
  stranded on the spinner forever. The key check does the same job without the
  trap.
- **Members drive the list, activity only decorates it** (see "Why it exists").
- **Idle rows still carry a number.** `lastRunSummary` gives "ran 3m12s · 5m
  ago"; a member that never ran says so. A bare "idle" is the kind of
  information-free label this whole surface exists to replace.

## Gotchas / edge cases

- **Trigger**: styling a stalled row with `--color-warning-soft` →
  **symptom**: the amber wash silently renders as nothing → **root cause**:
  that token does not exist. `index.css` defines `--color-warning` only; the
  soft variants are `--color-carbon-soft` / `--color-silicon-soft`. The row uses
  the repo's established `bg-[var(--color-warning)]/5` form instead (铁律 #1:
  no invented colour values).
- **Trigger**: reading the tool name straight out of `phase` →
  **symptom**: the row shows `mcp__x__read_file` → **root cause**: `phase` is
  the raw stored token; the row strips `tool:` and passes the rest through
  `friendlyToolName`. Note the phase TIMELINE deliberately does not — it goes
  through `phaseLabelKey`, matching the console it was lifted from.
- **Trigger**: an idle member whose `event_id` is null → **symptom**: expanding
  shows "no process record" rather than a spinner → **root cause**: correct —
  the activity row predates the `event_id` field, or the turn never produced an
  events row. The spinner is reserved for a fetch that is actually running.

## Newcomer traps

- `compareActivity` sorts stalled → running → queued → idle and ties by NAME,
  not by team-membership order. A roster that looks "wrongly ordered" is usually
  correctly ordered by attention.
- The panel renders its header (and the 0 count) even with no members; the empty
  state lives inside the list area, not in place of the whole shell.

## Related constraints

- 铁律 #1 — existing CSS tokens only, English-only code/comments.
- 铁律 #10 — this md ships in the same commit as the component.
