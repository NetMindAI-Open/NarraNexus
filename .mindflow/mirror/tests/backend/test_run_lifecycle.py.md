---
code_file: tests/backend/test_run_lifecycle.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

报告间隔用例同时打桩首报与常规间隔；新增钉住 5 s → 30 s 节奏且首报落在 10 s 宽限期内。

# Detached Run Shutdown Regressions

Tests reproduce the ownership gap before a run ID exists and verify resource
cleanup waits for finalization. Cancellation of shutdown callers, failed peers,
duplicate close calls and refused launches must not lose live work. A real
isolated SQLite recorder proves heartbeat updates and terminal stream flushes
still work while shutdown waits. No live process or user database is involved.

Job runs survive both HTTP and SSE disconnects, managed audit work participates
in drain, and repeated diagnostic intervals cannot cancel work. Resource cleanup
joins housekeeping finalizers and attempts remaining releases after worker errors.
