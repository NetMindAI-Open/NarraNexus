---
code_file: tests/backend/test_openai_compat_circuit_breaker_gate.py
last_verified: 2026-09-23
stub: false
---

# OpenAI Compatibility Run Admission

Uses an isolated real circuit-breaker repository and a fake run to verify paused
agents cannot start, probe claims are passed to the run, and setup failure returns
the claim. The fixture includes the production detached-task lifecycle owner.
An HTTP response may finish while its task remains owned; shutdown refusal must
return a half-open probe claim even when BackgroundRun construction succeeded.
