---
code_file: src/narranexus/platform/agent_runtime/admission.py
stub: false
last_verified: 2026-08-21
---

## 2026-08-21 — `claim_idle_users` 接受跨进程否决（prod 事故修复）

本控制器是**进程级**单例，所以它的视野是**片面的**：云端编排跑在 backend +
workers 两个进程，谁也不知道对方的 run。这对**准入**无害（各自管各自那份
配额），但对它顺带维护的**空闲记账**是致命的 —— [[executor_reaper.py]] 消费
的正是这份记账，于是把活在另一个进程里的 run 当成空闲容器停掉（2026-07-31
prod 事故，细节见 reaper 同日条目）。

`claim_idle_users(ttl, is_busy=...)` 新增注入式否决：调用方给一个跨进程真相
源，被否决的用户跳过。reaper 给的是 `_CullVeto` —— 它包住
`live_run_elsewhere`（读 `events` 表），把挡路的 run id 折成 bool，并按 run
去重审计行；**不是**直接给 `live_run_elsewhere`（那个返回 `Optional[str]`，
不满足 `BusyCheck` 协议，直接传会连带丢掉审计去重）。

**为什么否决必须在这个方法里面，而不是让调用方拿到名单后自己过滤** ——
claim 是**破坏性**的：返回名单的同时就删 `_idle_since` 戳。在外面过滤的话，
被跳过的用户戳没了，要等**本进程**下一次 `release()` 才重新打戳；而"主要在
workers 里跑"的用户在 backend 永远等不到那次 release → 容器**永不回收**。
那是把误杀换成泄漏，不是修复。

配套 `restamp_idle(user_id)`：给"claim 了但最终没动手"的调用方用（reaper 在
停之前又查了一次，发现用户忙了）。`setdefault` 而非赋值 —— 飞行期间可能有
一次 release 落了戳，那个更老的戳才是真的。

**`per_check_budget`（调用方自己的每候选超时）**：传了它，本层就**不发起**装不下
的那次判活 —— 直接按"预算耗尽、无判决扣留"处理。因为**记账住在调用方那一侧**
（reaper 把超时记成"判不出来"），被本层的整批 `wait_for` 中途取消的那次判活会从
统计里凭空消失，而按真实计时那恒定是每一轮的最后一个候选。由调用方**告知**而不
是本层 import —— 这一层不能反向依赖 reaper。

**否决在锁外跑**：它做 I/O（打 DB），握着 `_cond` 会堵死所有 acquire/release。
回锁后比对每个候选的戳还是不是刚才判的那一个，防住飞行期间用户重新活跃。
整批有 `_VETO_BUDGET_S` 预算：DB 卡死时**停的是回收，不是 reaper 自己**
（事故教训 #4：光看"任务还在"不算活性检查）—— 没来得及判的一律算忙，戳留着。
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
