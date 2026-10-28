---
code_file: src/narranexus/platform/message_bus/_bus_activity.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 沉默的一轮记在活动行上（`note_silent_turn` / `last_turn_was_silent`）

团队房里一轮没调 `message_team`,以前由 trigger 往 bus 贴一条 `system_undelivered`
(prod 团队房 21 天里 16% 的行是它);prompt 说沉默合法、房间却记成失败,agent 就学会硬说。
现在 `note_silent_turn(db, agent, channel)` 读回该 (agent, channel) 行,把
`{"phase": "silent"}` **追加**到 `finish()` 刚写完的 steps 尾部(保留本轮时间线,
同样受 `MAX_STEPS` 截断),无 bus 行、无 schema 改动;下一轮 `start()` 重写 steps
自然清掉。`last_turn_was_silent(row)` 是读侧判定,[[../../../backend/routes/teams]]
的 `_member_activity` 在 idle 条目上输出 `last_turn_silent`。永不抛。
写侧只给 trigger(直接 import 本模块);读侧判定 `last_turn_was_silent` 经 [[activity]] 外露。
测试:`test_bus_activity.py` 末四条。


## 2026-07-30 — bind the turn's event_id onto the activity row

`TurnActivity` gained `note_event_id(event_id)` and the row a matching
`event_id` column. Semantics: whichever `events` row backs the CURRENT/most
recent turn for this (agent_id, channel_id) — `start()` resets it to `None`
(a new turn must not inherit the previous turn's id), `note_event_id` writes
it once the runtime surfaces it, and `finish()` deliberately leaves it in
place (same "keep `steps` after the turn ends" reasoning: the team UI wants
to fetch the JUST-finished turn's full event_log via the existing event-log
endpoint, not only a live one). This is the team-room UI's missing link
between the cheap activity mirror and the heavier `events`/event-log
pipeline it otherwise avoids standing up.

## 2026-07-28 — the heartbeat became a heartbeat

`updated_at` was named a heartbeat but was written ONLY by the runtime's
`on_progress` callback, i.e. it recorded *traffic*, not *liveness*. The reader's
90s `ACTIVITY_STALE_SECONDS` window meanwhile means "the trigger process died".
A healthy long tool call or a model thinking silently for minutes therefore aged
the row out and the team chat downgraded a working agent to "queued" — observed
on prod 2026-07-23 (run_6c8598cf, 25 min, shown as queued throughout).

Now `TurnActivity` (scope it with `turn()`) owns a turn end to end:

- a **timer** task refreshes `updated_at` every `HEARTBEAT_INTERVAL_SECONDS`
  (30s) regardless of whether the runtime emits anything, so the 90s window
  again means only "the process is gone". The task is paired with an
  `add_done_callback` (incident lesson #2) and swallows its own DB errors — a
  missed beat must cost a beat, not the run.
- phase writes are now **change-only** (the old code also wrote every ~2s as a
  pseudo-heartbeat); the timer covers liveness, so the DB write rate drops.
- `steps` accumulates the turn's phase transitions in memory and writes the
  whole capped list, so no read-modify-write per phase. Capped at `MAX_STEPS`
  dropping from the front, with the drop counted — the UI states what it isn't
  showing rather than silently truncating.
- `mark_idle` KEEPS `steps`: with the idle `updated_at` (= finish time) the room
  can show what an agent just did, not only what it is doing.

`is_stalled()` is the other half. Readers used to have only `is_live()`, so a
started-then-silent turn was indistinguishable from one that never started, and
[[teams]] reported both as "queued". Stalled is now its own state all the way
to the UI. Fixing a stall is NOT a matter of widening the window — that would
just make a genuinely dead trigger lie for longer.


# _bus_activity.py — live "what is this agent doing" for team rooms

## Why it exists

A team-room agent runs in the background via [[message_bus_trigger]] — the team chat UI has
no WebSocket stream to it (unlike the single-agent path, which gets `events`/`event_stream`/
Broadcaster telemetry from `BackgroundRun`). This module is a **cheap status mirror**: the
trigger writes running/phase/heartbeat into `bus_agent_activity` around + during a run, and
`plugins/builtin.teams/src/narranexus_plugins/teams/routes.py::get_team_chat` reads it to show running / phase / elapsed.

Deliberately NOT the `events` pipeline (which is WS-only and heavier). One row per
(agent_id, channel_id); `state` flips `running`→`idle` at turn end.

## Shape

`mark_running` (start) → `update_phase` (thinking / tool:<name> / replying, throttled by the
trigger's `_make_activity_progress`) → `mark_idle` (end, in a `finally`). `is_live(row)` is
the reader-side guard: a `running` row whose `updated_at` heartbeat is older than
`ACTIVITY_STALE_SECONDS` (90s) reads as not-live (the trigger process died mid-run).

## Gotchas

- Writes go through the dialect-safe `AsyncDatabaseClient` (`get_db_client()`), not the raw
  bus backend — `_upsert` is update-or-insert on the composite PK (agent_id, channel_id).
- Progress is fed by the opt-in `on_progress` callback on `run_collector.collect_run` (only
  the team branch passes one; every other trigger passes None → zero overhead).
- Status writes must never break delivery — the trigger swallows their errors.
- `note_event_id` is write-once per `TurnActivity` instance (`self._event_id`
  guards it) — a second call with a different id is silently ignored, not
  overwritten. Only `start()` (a new turn) resets it.

## 2026-08-12 — `elapsed_seconds`:调用方真正想问的那个问题

roster 渲染此前跨模块拿 `_parse_ts` 这个私有名字用,而它真正要的是「这一轮跑了多久」。
私有解析器外泄会让每个调用方各自推导一遍答案,所以改成导出语义本身。

口径写死在这里:从 **`started_at`**(本轮开始)算,**不是 `updated_at`**(心跳,永远
约等于现在)。
