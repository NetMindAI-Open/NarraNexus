---
code_file: frontend/src/lib/inboxOrder.ts
last_verified: 2026-08-18
---

# inboxOrder.ts — microsecond-accurate inbox message ordering

``compareInboxMessages`` orders inbox messages chronologically (oldest
first) by comparing created_at as STRINGS. The backend serialises created_at
as a microsecond-precision ISO string (inbox route _to_iso, "sorts
lexicographically in time order"); string compare preserves the 1µs gap the
writer puts between a turn's inbound and reply, whereas
``new Date(created_at).getTime()`` truncates to milliseconds and collapses
it — the "reply above its question" bug, worst on WeChat. Chosen over a
message_id tie-break: string compare gives full microsecond fidelity and
correct Q1 A1 Q2 A2 order even for turns that land in the same millisecond,
which a prefix tie-break would reorder to Q1 Q2 A1 A2. Guarded by
lib/__tests__/inboxOrder.test.ts.

## 2026-08-18 — 1µs 间隔的产出方改名

注释里那个「服务端把一轮的两行错开一微秒」的产出方从 `channel_inbox_writer` 变成了
`channel/inbox_recorder.py`。排序逻辑本身没变，仍然依赖**按字符串比较** created_at ——
`new Date(x).getTime()` 会截断到毫秒，把一轮的 inbound/reply 压成相等值。

同日相关：沉默轮次的线程 `last_message_at` 此前盖在"回复时隙"（`now + 1µs`）上，而那一轮
没有回复行 —— 面板按这一列排序，所以沉默轮次会排在同一瞬间真答了的轮次前面。已改为取实际
写出的那一行的时间戳，见 [[inbox_recorder.ts]] 对应的后端镜像。
