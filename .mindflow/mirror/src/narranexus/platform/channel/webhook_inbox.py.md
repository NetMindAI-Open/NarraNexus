---
code_file: src/narranexus/platform/channel/webhook_inbox.py
last_verified: 2026-09-07
stub: false
---

# channel/webhook_inbox.py — the webhook transport inbox

## Intent

`channel_webhook_events` as a queue: the backend API process pushes a channel's inbound webhook events, the channel trigger (workers process) pulls and claims them in arrival order (`claimed_at`), so the hand-off crosses processes. A DB table on purpose — one host today, the seam a broker replaces later (rule #20). `purge_claimed` keeps the audit window short.

## 2026-09-07 — atomic claim; retention purge by cutoff in one statement

pull() claims with UPDATE … WHERE id=? AND claimed_at IS NULL and skips a row whose update affected 0 rows — two readers (rolling restart overlapping a draining process) can no longer both deliver the same event; at-most-once stays the deliberate choice. purge_claimed(channel, older_than) deletes CLAIMED rows older than the cutoff in one raw statement (%s placeholders, both dialects, MySQL twin test) and never touches unclaimed rows; the cutoff datetime is serialized with isoformat() to match what _serialize_value stores.
