---
code_file: src/narranexus/platform/message_bus/activity.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 只外露读侧 `last_turn_was_silent`

路由侧(`_member_activity`)读沉默标记走这个门面;写侧 `note_silent_turn` 与
`TurnActivity`/`turn` 一样**留在 [[_bus_activity]]**,trigger 直接 import 私有模块
(与同文件其余 4 处一致)。门面「只公开读侧」的契约不变。


## 2026-07-28 — `is_stalled` / `parse_steps` join the read surface

The write side is now `TurnActivity` / `turn()` rather than the three loose
`mark_*` functions, and it stays private for the same reason as before. Two
readers were added: `is_stalled` (running row, dead heartbeat) so a route can
tell "started then went quiet" from "never started", and `parse_steps` so the
`steps` blob is normalised in ONE place — a route must never hand a raw JSON
column to a serialiser.


# activity.py — public read surface for team-room live activity

## Why it exists

Same layering rationale as [[attachments]]: the team-chat GET route needs
``get_channel_activity`` / ``is_live`` but must not import the private
[[_bus_activity]] module cross-package. Only the READ side is re-exported —
the write side (``mark_running`` / ``update_phase`` / ``mark_idle``) belongs
exclusively to [[message_bus_trigger]] inside the package and stays private
on purpose: no route should ever fabricate an agent's live status.
