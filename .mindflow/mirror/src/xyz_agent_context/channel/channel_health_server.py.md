---
code_file: src/xyz_agent_context/channel/channel_health_server.py
stub: false
last_verified: 2026-08-04
---

## 2026-08-04 — 隔离态必须在健康面上说得清

`ChannelTriggerBase.health_snapshot()` 增补 `breaker_isolated_keys` /
`unstartable_keys`。起因：
[[channel_trigger_base.py]] 的两道闸让「凭据在册但故意不跑」成为稳态，而
`subscriber_count`（读 `_subscriber_tasks`）会掉到 0、`subscriber_keys`
（读 `_subscriber_creds`，被挡的 key 每轮照样刷新）却仍列着——两个字段互相
矛盾，运维 curl 出来既看不出少的那个订阅器去哪了，也分不清「凭据被删」和
「被隔离」。这正是教训 #4 说的 L2 盲区：进程活着、worker 活着、队列是空的，
一切「正常」，但某个 agent 的 IM 通道其实被停在那儿。两个列表把
`subscriber_count < len(subscriber_keys)` 的缺口解释完整。审计表里有事件行
可查，但那是事后翻表，不是健康面。

## Why it exists

One `/healthz` endpoint reporting a per-channel snapshot for EVERY consolidated
channel trigger. Generalised from the old `lark_module/_health_server.py`
(deleted 2026-07-08), which only snapshotted a single `LarkTrigger`. Now that
every channel runs inside one supervisor process, a single aggregated health
server also closes the old observability gap where only Lark had an endpoint.

## Design decisions

- **One public health seam.** The server calls
  `ChannelTriggerBase.health_snapshot()` and does not inspect trigger-private
  subscriber, worker, queue, audit, or breaker dictionaries. Snapshot ownership
  stays with the class that owns those fields, so adding observability state no
  longer forces the endpoint and every duck-typed test double to evolve in
  lockstep. Lark's optional WS timestamp is handled inside the base snapshot.
- **Overall status = ok only if every channel is ok.** Any channel still
  `starting` (no audit repo yet / not running) makes the aggregate `degraded`.
- **Best-effort, never blocks startup.** If fastapi/uvicorn aren't installed
  (tests, stripped image) `start_channel_health_server` returns None and the
  supervisor runs without health. `count_by_type` failures degrade to empty
  counts, never raise.
- **Port 47831 unchanged** from the Lark server (quiet range, no collision with
  the 74xx fleet; container-internal, not published).

## Upstream / downstream

- **Upstream**: `run_channel_triggers` calls `start_channel_health_server(started)`.
- **Downstream**: each trigger's `_audit_repo.count_by_type` (L3 event counts).

## Gotchas

- Started ONLY by the supervisor now. `LarkTrigger.start()` used to spawn its own
  health server; that was removed to avoid double-binding port 47831.
