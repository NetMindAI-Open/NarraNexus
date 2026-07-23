---
code_file: backend/routes/admin_runtime.py
stub: false
last_verified: 2026-07-22
---

## 2026-07-22 — added GET /api/admin/runtime/workers (Workers card liveness)

Second read-only L2 endpoint in this module. Reads the latest
`worker_supervisor` heartbeat row from `service_audit` (via
`ServiceAuditRepository.last_heartbeat`, falling back to the `started` row on a
cold boot) and returns a per-worker snapshot `{available, heartbeat_age_seconds,
workers:[{name,state,restart_count,last_error}]}`. Feeds the desktop System
page's single "Workers" card ([[ServiceCard.tsx]] / [[run_worker_supervisor.py]]):
the four merged workers share one process, so the process dot can read "running"
while a sub-worker crash-loops — this exposes each sub-worker's state +
cumulative restart_count. Same discipline as `/status`: every sub-step guarded,
never 500s (DB blip or absent supervisor → `available:false`). Auth:
`/api/admin/*` needs a JWT in cloud but is bypassed in desktop local mode, which
is exactly (and only) where the Workers card renders.

## Why it exists

`GET /api/admin/runtime/status` — 只读 L2 可观测端点,给 executor 调度/资源系统
一个"分钟级发现问题"的窗口(scheduling-resource 设计 §9)。

三段拼装,每段独立容错,**任何单段失败都不让端点 500**:
- `admission`: `get_admission_controller().snapshot()`(活跃用户/loop、各 cap、
  排队深度、free_mem vs 内存阀)。
- `executors`: 经 broker `GET /executors` 取活容器列表;无 `BROKER_URL` 或 broker
  不可达 → `[]`(handler 层 try/except 兜底,`_get_executor_list` 可抛)。
- `audit_counts`: `ExecutorAuditRepository.counts_since(近1h)`,看 OOM/cull/
  orphan-reap 速率。

注入接缝:`get_db_client`(db_factory)、`get_admission_controller` 都是模块级名,
测试可 monkeypatch。本轮未加鉴权(管理端点,部署侧应在网关/反代限制访问 —— 待办)。
