---
code_file: src/narranexus/kernel/plugins/activation.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2b.4）— 激活事件：首个事件才 import + `activate(ctx)`

启动只登记声明式贡献；`Activator.register(manifest)` 记下它订阅的事件（`derive_activation_events` +
`validate_event_name` 八种前缀），`fire(event)` 激活所有未激活订阅者：线程池带超时 import
`nxplugins.<id>`、调 `activate(ctx)`（可 async，带超时）。每进程只激活一次；失败进 `on_crash`（宿主接
`RegistryStore.record_crash`，第二次自动 disable）且**永不向触发方抛**——打开一个页面不能因为某插件坏了
而失败。事件不重试已失败者，`activate_now` 是显式重试（工场页/自我扩展模块用）。`deactivate` 调可选
`deactivate(ctx)` 后无论如何 `ctx.dispose()`。

## 2026-09-04 · UI slot points (batch 3d.2)

`EVENT_PREFIXES` accepts `onRenderer:`, `onTimelineEvent:`, `onSlot:` (the frontend slot-point gates fire them).

## 2026-09-07 — context factory runs off the loop, bounded

context_factory(manifest) is awaited through run_in_executor with context_timeout_s (15 s): the backend's factory loads plugin settings from the DB, and a stalled connection used to park the host event loop — and /health — for the whole hang (the v1.7.16 outage shape).
