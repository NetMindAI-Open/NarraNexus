---
code_file: tests/module/wechat/test_wechat_send_delivery.py
last_verified: 2026-07-03
---

# test_wechat_send_delivery.py — part of the 2026-07-03 wechat silent-reply fix pack

See the dated 2026-07-03 entries in message_bus_trigger.py.md,
wechat_sdk_client.md and channel_trigger_base.md for the incident and
design decisions. The three test files split by layer: bus skip-prefix
guard (registry/filesystem-driven), wechat send hardening (BMP sanitize +
failure logging + prompt hint), and unparsed-ingress audit.
