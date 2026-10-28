---
code_file: backend/host_events.py
last_verified: 2026-09-07
stub: false
---

# backend/host_events.py — host events from routes

## Intent

`emit_host_event` (never raises) and `notify_user_runnability_changed` — login, quota top-up and provider/slot saves fire `onDidChangeUserRunnability` instead of importing job_recovery; builtin.job's hook does the re-arm.

## 2026-09-07 — UnknownEntry propagates

A listener's failure is still swallowed with a warning (the request must not break because a plugin's reaction did), but an event that does not exist (typo, renamed hook) raises: swallowing it turned 'jobs silently stop re-arming after login' into one WARNING line.
