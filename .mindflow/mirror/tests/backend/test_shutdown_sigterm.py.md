---
code_file: tests/backend/test_shutdown_sigterm.py
last_verified: 2026-09-23
stub: false
---

# SIGTERM Regression

Starts an owned subprocess with temporary plugin and DB paths, launches synthetic
work through the real chat WS, disconnects, then sends SIGTERM. It proves the
lifespan stays alive, heartbeats advance and housekeeping stays running until
the test releases the run. Terminal persistence, thinking flush and resource
closure must precede process exit. Harness deadlines only bound a failing test;
production agents have no deadline. No existing service receives a signal.
