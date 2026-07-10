---
code_file: tests/channel/test_content_fingerprint_dedup.py
last_verified: 2026-07-03
---

# test_content_fingerprint_dedup.py — X1 double-reply guard

Locks the layer-4 contract: same fingerprint + new id rejected within the
window, expiry restores acceptance, disabled-by-default, per-agent
partitioning, empty fingerprint bypass, and NarramessengerTrigger's window
covering the platform's 15-min re-dispatch deadline.
