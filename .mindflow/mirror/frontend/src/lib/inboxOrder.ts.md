---
code_file: frontend/src/lib/inboxOrder.ts
last_verified: 2026-07-03
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
