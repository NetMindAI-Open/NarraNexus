---
code_file: tests/agent_framework/test_anthropic_usage.py
last_verified: 2026-07-28
stub: false
---

# test_anthropic_usage.py — Anthropic token-total regression tests

Locks the shared normalizer against both SDK-object and CLI-dictionary usage
shapes. The assertions make cache creation and cache reads part of the input
total and keep the missing-usage zero shape explicit.
