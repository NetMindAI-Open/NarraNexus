---
code_file: src/xyz_agent_context/module/lark_module/_health_server.py
stub: false
last_verified: 2026-04-21
---

## Why it exists

Operators without EC2 log access need a single-shot way to answer
"is the Lark trigger alive and what is it doing right now?". This
FastAPI endpoint gives them exactly that, served on a quiet port
(47831) from inside the trigger's own asyncio loop.

## Design decisions

- **Embedded**, not a separate service: runs in the trigger's event
  loop so the health view is self-consistent (no cross-process data
  fetch).
- **Port 47831**: quiet range (47xxx has near-zero IANA
  registrations), easy to remember (`831` echoes Lark's `7830`
  convention), and no collision with the NarraNexus fleet of
  `74xx` ports.
- **Container-internal**: `compose.yml` does NOT publish to host —
  operators hit it via `docker exec`. Reduces exposure surface.
- **Best-effort**: if FastAPI/uvicorn aren't installed (tests, minimal
  image) the server silently returns None; the trigger keeps running.
- **Pure `build_health_payload` split out**: unit-testable without
  spinning uvicorn.

## Upstream / downstream

- **Upstream**: `LarkTrigger.start()` calls `start_health_server(self)`
  and stashes the returned task in `_monitor_tasks` so `stop()`
  cancels it.
- **Downstream**: reads `LarkTriggerAuditRepository.count_by_type` for
  the 1-hour event summary; reads trigger's own state counters for the
  rest.

## Gotchas

- `access_log=False` + `log_level="warning"` keeps the health server
  from spamming `INFO`-level uvicorn access logs on every heartbeat
  from a dockerised uptime monitor.
- Future expansion (admin UI, metrics scrape) should keep the
  container-internal convention and add separate routes on the same
  port rather than publishing to host.
