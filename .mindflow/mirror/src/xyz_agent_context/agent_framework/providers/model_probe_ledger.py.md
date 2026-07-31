---
code_file: src/xyz_agent_context/agent_framework/providers/model_probe_ledger.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 (review 修补) — `passing_models` 唯一读取口

`extra` 不变式（网关独有模型永不上 netmind/system_pool 卡）以前散落在多个
comprehension 里，apply 路径漏掉过滤直接把网关独有 id 灌进付费卡。现在
`passing_models(models_map, protocol, include_extras=False)` 是**唯一**的
按协议 pass 名单出口，`ledger_models`/`apply_ledger_to_db`/`res.lists` 全部
经它——下一个读取方想绕都难。

## 2026-07-30 — DB 成为耐久载体，文件降级为种子

云端容器每次部署都把 ledger 文件重置回 release 快照，复测历史（tested_at 时钟、
pass→fail 翻转）全部丢失——这是"PASS 终身信任"问题的一半根源。新增
`model_probe_ledger` 表（一行一个 source），`load_ledger_db` / `save_ledger_db`
是异步 DB 层；写方（daily runner、Update models 按钮）DB 优先加载、双写回存，
文件只做首跑种子和 `model_catalog` 的同步读路径。注意 DB driver 会把时间样式
字符串解析成 datetime，load 时归一化回 isoformat。

# agent_framework/providers/model_probe_ledger.py — the probe dedup cache (read/write)

## Why it exists

The pure read/write layer for `model_probe_ledger.json` — the committed record
of, per (provider source, model id), which protocols (`openai`/`anthropic`) the
model actually answers on. Split from [[model_sync]] (which owns the probing) so
[[model_catalog]] can READ the ledger without importing the httpx/probe code
(avoids a circular import and keeps the read path dependency-free).

## How it works / design

- The JSON file is **committed** = the release-time snapshot, so a fresh local /
  DMG install ships with known-good per-protocol lists and never probes on first
  run. The cloud daily job and the local "Update" button rewrite it at runtime
  (the DB `user_providers.models` rows are the durable store; the file is the
  dedup cache that survives until a redeploy reseeds it from the committed copy).
- `ledger_models(source, protocol)` returns only ids where `entry[protocol] ==
  "pass"`. `system_pool` is aliased to the `netmind` entry (same backend).
- `save_ledger` writes sorted, pretty JSON so diffs stay clean across runs.

## Gotchas

- Missing/corrupt file → empty skeleton (caller falls back to hardcoded
  defaults), so a wiped runtime never hard-fails.
- It's a `.json` data file, not source — only the model ids + pass/fail +
  display/context metadata live here, no logic.
