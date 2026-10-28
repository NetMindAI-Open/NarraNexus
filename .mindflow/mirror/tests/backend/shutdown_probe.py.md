---
code_file: tests/backend/shutdown_probe.py
last_verified: 2026-09-23
stub: false
---

# Isolated Shutdown Probe

Runs real Uvicorn, backend lifespan, WS handler and BackgroundRun recorder against
a temporary SQLite file. Only the LLM work and unrelated startup services are
substituted. Files coordinate test work completion and prove housekeeping and DB
close follow finalization. It never uses a real agent or user database.
