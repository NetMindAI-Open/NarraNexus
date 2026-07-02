---
code_file: src/xyz_agent_context/agent_runtime/admission.py
stub: false
last_verified: 2026-06-18
---

## 2026-06-18 — snapshot() + queue-depth observability

新增只读 `snapshot() -> dict`(active_users/active_loops/queue_depth/各 cap/
free_mem_mb/per_user_loops/enabled),供 `GET /api/admin/runtime/status` 暴露
L2 监测态。`acquire` 用 try/finally 维护 `self._waiting` 计数(进 `wait_for`
前 +1、出后 -1),让排队深度可观测。**纯观测,不改准入行为。**

## Why it exists

Two-level concurrency admission gate. One user can drive many agents at
once (chat + scheduled jobs + message-bus interactions), so without a cap
the box OOMs. This bounds it — and does so **only by delaying the START**
of a run (queueing), never by interrupting a running loop (binding rule
#14).

## Model

Four knobs (env-tunable, prod-sizing defaults in cloud: 20/5/50/6144):
- `MAX_CONCURRENT_USERS` (global, distinct active users; default 20 in cloud)
- `MAX_LOOPS_PER_USER` (per-user simultaneous loops — main anti-starvation; default 5)
- `MAX_CONCURRENT_LOOPS` (global total loops — the real RAM ceiling; default 50)
- `MIN_FREE_MEM_MB` (dynamic guard — hold new loops when free RAM is low,
  catches subagent memory spikes the loop counts can't predict; default 6144)

A run is admitted only when ALL hold (`asyncio.Condition.wait_for`);
otherwise it queues. Released slot → `notify_all` re-checks waiters.

## Decisions / gotchas

- **Local/desktop = unlimited (no-op).** Defaults are None + mem-guard 0
  unless `get_deployment_mode()=="cloud"`, so `bash run.sh` / DMG behave
  exactly as before (binding rule #7). Env vars override either way.
- **State behind the controller instance (a seam)** so it can move to
  Redis when the orchestrator scales to >1 replica (binding rule #20).
  Today it's an in-process singleton (`get_admission_controller`).
- **Integration point = the client seam** (`InProcessAgentRuntimeClient`
  `run_and_collect` / `run_stream` wrap the run in `controller.slot(user_id)`).
  Covers all trigger paths (job/bus/lark/slack/telegram/chat). NOTE: the
  backend WS path drives `BackgroundRun.runtime.run()` directly and does
  NOT yet go through the gate — follow-up to wrap it too.
- Fairness is cap-based (per-user M bounds any one user); a strict
  round-robin out-queue is a future refinement.
- `_free_mem_mb` reads `/proc/meminfo`; returns +inf off-Linux so the
  guard never blocks on desktop.

## Idle bookkeeping (feeds the executor reaper)

The controller also tracks WHEN each user dropped to zero active loops
(`_idle_since`, stamped in `release`, cleared in `acquire`), using an
injected `clock` (default `time.monotonic`, swappable for deterministic
tests). `claim_idle_users(ttl)` atomically returns + un-tracks users idle
≥ ttl. This is the controller's ONLY outward knowledge of culling — it
stays ignorant of brokers/executors. The reaper
([[executor_reaper.py]]) is the coordinator that consumes this and calls
the broker to stop them. Single-responsibility: controller = state,
reaper = WHEN, broker_client = HOW.
