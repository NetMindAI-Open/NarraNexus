---
code_file: tests/module/test_mcp_caller_identity.py
last_verified: 2026-09-22
stub: false
---

# MCP identity regressions

The suite pins caller correction and the bearer record's positional contract.
Conversation scope is field 10; identity_token remains field 9 and event_id
remains field 8. Round-trip and dispatch-stamping tests prevent one adapter
from silently losing new identity facts when it forwards only the bearer.
