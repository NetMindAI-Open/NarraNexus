---
code_file: src/narranexus/engine.py
last_verified: 2026-09-07
stub: false
---

# engine.py — the headless Engine

`Engine.load(dist, registries, cloud, host_version, store, bus)` resolves a distribution (path/file/resolution/None), registers the builtin contributions when the registries are empty, runs the same `hosts.boot.boot("backend", distribution=...)` the backend host uses and marks it healthy; a second boot of frozen registries is refused. `run_turn(agent_id, user_id, input, ...)` streams `AgentRuntime.run` messages with the engine's registries and db; `agents(user_id)` = own + public agents via `AgentRepository.find`; `events()` = the `EventBus`; `use_db()` borrows a client (not closed by `close()`); `close()` releases an owned db. `run(coro)` is a script convenience.

## 2026-09-07 — mark_healthy is the embedder's statement

Engine.load() no longer calls report.mark_healthy() right after boot ('booted' is not 'proven healthy', and the LKG snapshot now moves on health); the embedding host calls engine.mark_healthy() after its own probe or first successful turn.

## 2026-09-07 — Engine.load boots, nothing else registers

The register_all call before boot is gone; boot() is the only registration.

## 2026-09-07 — DistLike 死常量删除（round-2 K2-M1）

无引用的字符串常量。
