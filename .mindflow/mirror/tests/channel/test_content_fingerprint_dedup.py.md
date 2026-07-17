---
code_file: tests/channel/test_content_fingerprint_dedup.py
last_verified: 2026-07-13
---

# test_content_fingerprint_dedup.py — X1 double-reply guard

Locks the layer-4 contract: same fingerprint + new id rejected within the
window, expiry restores acceptance, sliding window on hit,
disabled-by-default, per-agent partitioning, empty fingerprint bypass.

2026-07-13: the trigger-policy assertion changed direction. The gateway
NarramessengerTrigger (window ≥ 16 min against the platform's 15-min
re-dispatch deadline) was retired for the Direct-Matrix adapter, whose
event_ids are stable across /sync replays — so the file now asserts
MatrixTrigger keeps CONTENT_DEDUP_WINDOW_SECONDS == 0 *on purpose* (a
content window there would eat legitimate user re-sends). The mechanism
tests stay: any future gateway-style trigger that mints new ids per
dispatch must opt back in with a deadline-covering window.
