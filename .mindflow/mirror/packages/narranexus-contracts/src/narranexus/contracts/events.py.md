---
code_file: packages/narranexus-contracts/src/narranexus/contracts/events.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-03（批 2e）— `HOST_EVENT_PAYLOADS` / `host_event_params`

每个宿主事件对应的 TypedDict；内核据此为每个事件声明 HookSpec（参数 = TypedDict 的键），插件的
`backend.hooks` 才有词表可对。

## 2026-09-03 — 宿主事件词表（hooks kind 的契约）

九个事件名按 VS Code 指南 `onDid|onWill + Verb + Subject` 命名，payload 用 TypedDict。
批 0 只声明词表与 `kernel/events/bus.py`；平台在观察点 `emit` 是批 2/3（D9）。
总线对不在 `HOST_EVENTS` 且未 `declare` 的名字抛 `UnknownEntry`，让拼错的订阅 fail-loud
而不是永远收不到。

## 2026-09-04 · ingress triggers (batch 3c.3)

`onDidResolveBootstrapGreeting` (`BootstrapGreetingEvent`: agent_id/user_id/instance_id/greeting/turn_started_at) — fired by step 1 after the platform resolves a bootstrap greeting; builtin.chat's `plugin_hooks` seeds it. First host event that is a *request to a capability owner* rather than pure observation.

## 2026-09-04 · services + host hooks (batch 3c.6)

`onDidChangeUserRunnability` (user_id), `onDidChangeAgentName` (db, agent_id, old_name, new_name), `onDidSettleAgentName` (db, agent_id, name), `onWillExportManagedChannels` (db) — the request-style host events that replaced the last platform→builtin imports; `db` rides in the payload so listeners write on the caller's client.

Batch 6b.3: new host event `onDidStartBackend` with `BackendStartedEvent(db)` — the backend fires it once from its post-start background task so plugins can seed catalogs off the startup critical path (builtin.teams seeds the team marketplace there).
