---
code_file: tests/agent_framework/test_codex_sdk_v2_init.py
last_verified: 2026-07-28
stub: false
---

# test_codex_sdk_v2_init.py — Codex SDK static and notification contracts

In addition to the SDK import, driver, and configuration contracts, this test
module verifies the token-usage handoff required by the app-server protocol:
the latest per-turn snapshot replaces earlier cumulative updates, is attached
once to a completion that lacks usage, and never overwrites native completion
usage.
