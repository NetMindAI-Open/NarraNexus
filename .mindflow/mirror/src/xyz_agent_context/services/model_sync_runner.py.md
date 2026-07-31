---
code_file: src/xyz_agent_context/services/model_sync_runner.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 (下午) — 网关目录并集 + free 门 + [MODEL-DRIFT]

pass 开头单次取 free-tier 网关 served 列表：作为 `extra_models` 并进 netmind
探测（网关独有模型有了判定与 TTL 复测）；sync 成功后
`refresh_free_tier_models(db, ledger=)` 先跑（其内部的 gate 调用把 netmind_free
条目写进内存 ledger）**再**双写保存——两个载体都带门后名单，gate 只跑一次。
末尾 `compute_drift`（仅全协议 FAIL 算漂移）非空时打 `[MODEL-DRIFT]` WARNING
（deploy 仓 watcher 的告警签名）；durable 记录走 run_loop 的每-pass
`heartbeat(summary+drift)`（started/stopped 同步补齐，L2，教训 #4/#5），
drift 是例行对账输出，不占 error 事件。网关不可达 = extras 传 None，
现有 extra 条目原样保留，free 卡照旧不动。

## 2026-07-30 — DB ledger + 嫌疑复测接线

pass 顺序变为：`auto_migrate`（runner 是独立容器，不能赌 backend 先建表）→
DB 加载 ledger（无则文件种子）→ `load_suspects` 注入 `sync_source(suspects=)` →
双写 ledger（文件 best-effort + DB）→ 按 source 清嫌疑 → `apply_ledger_to_db`
显式传入内存 ledger（旧代码在 apply 里从文件重读——云端文件写失败时会拿旧
快照覆盖 DB，已修）。日志行加了 revalidated/flipped。

# services/model_sync_runner.py — daily driver for provider model auto-sync

## Why it exists

[[model_sync]] is the engine (catalog fetch + probe + ledger); this is the thing
that *runs* it on the cloud. The repo has no general scheduler, so this is a
small standalone service (same shape as [[module_poller]] / message_bus_trigger):
a daily-at-05:00-UTC loop that refreshes the ledger and overwrites every user's
provider model lists.

## How it works / design

- `run_once()`: for each source with a key in env (`NETMIND_API_KEY` →
  netmind+system_pool, `OPENROUTER_API_KEY`, `YUNWU_API_KEY`), call
  `model_sync.sync_source` (probe new/failed, refresh the ledger), then
  `model_sync.apply_ledger_to_db(db)` — one bulk dialect-safe `db.update` per
  (source, protocol) overwriting **all** users' rows. One source failing is
  logged and skipped; it never aborts the rest.
- `run_loop()`: sleeps to the next 05:00 UTC and repeats; survives any single
  pass crashing.
- Two run modes: `python -m …model_sync_runner` (one pass — used by the release
  `make models-refresh` step + dev) and `… --loop` (the cloud compose service
  `narranexus-model-sync`).

## Gotchas

- The probe result is a backend property, not per-key — so one pass with the
  platform key updates every user. system_pool rows are overwritten from the
  netmind ledger entry.
- Ledger write is best-effort (read-only container rootfs just loses the dedup
  cache; the DB rows are the durable output, next run re-probes).
- Lifecycle is logged (start/per-source/done/error); a dedicated audit table is
  a future nicety, not wired yet.
