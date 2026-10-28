---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/plugin_hooks.py
last_verified: 2026-09-04
stub: false
---

# job_module/plugin_hooks.py — builtin.job hooks

## Intent

`onDidChangeUserRunnability` → `job_recovery.schedule_user_no_quota_rearm` (the edge-triggered PAUSED_NO_QUOTA re-arm login / quota / provider routes used to import). Imported lazily inside the impl so test monkeypatches on `job_recovery` keep working.
