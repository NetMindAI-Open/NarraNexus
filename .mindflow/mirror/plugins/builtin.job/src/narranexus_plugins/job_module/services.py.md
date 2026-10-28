---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/services.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r2 I-B）— 新增 `jobs.resume_for_principal`

`resume_for_principal(db, user_id, paused_reasons)` 惰性委托 `job_recovery.resume_jobs_paused_for_principal`，
注册在 `JOB_RESUME_FOR_PRINCIPAL` 下。admin reinstate 路由经 `plugin_services.try_job_resume_for_principal()`
取用——backend 不 import 本插件；builtin.job 未加载时取到 None，路由在响应里报 `jobs_resume_error`。


# job_module/services.py — builtin.job services

## Intent

Exposes `jobs.instances` (a `JobInstanceService` factory over the caller's db; onboarding and Arena provisioning create jobs through it) and `jobs.run_once` (`run_once.run_job_once`; the Manyfold sync route runs a job on demand through it).
