---
code_file: src/xyz_agent_context/agent_framework/providers/model_sync.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — 免费卡的目录不走 ledger

`apply_ledger_to_db` 刻意**不覆盖** `netmind_free` 行。免费额度经我们自己的
网关打到 NetMind，而网关只路由（也只定价）配置里那 15 个模型 —— 把上游 43 个
的探测结果盖上去，等于在下拉框里放一堆一调就 400 的选项。

免费卡的列表由 `refresh_free_tier_models` 直接问网关要，挂在同一趟日更里，
这样改了 `pricing/models.json` 重新部署后，存量卡的下拉框会自动跟上。

# agent_framework/providers/model_sync.py — auto-discover & probe provider models

## Why it exists

We used to hardcode each provider's model list in [[model_catalog]]. That rots:
NetMind/OpenRouter add models weekly, and — proven by experiment — an
aggregator exposing a model on its OpenAI endpoint does **not** mean it answers
on its Anthropic endpoint (NetMind: 43/43 openai but only 23/43 anthropic, with
no signal in the catalog). This module discovers the truth at runtime: fetch the
catalog, **probe** which models actually answer per protocol, and feed the
passing lists into the per-(source, protocol) model lists.

## How it works / design

- **CatalogSource** per aggregator: a catalog `fetch()` + the two probe base URLs
  + the protocols to probe. In scope: `netmind` (+ `system_pool`, same backend),
  `openrouter`, `yunwu`. Out of scope (OAuth CLIs self-track; custom_* arbitrary).
- **Probe** = a 4-token completion; HTTP 200 ⇒ `pass`. OpenAI →
  `{base}/chat/completions`; Anthropic → `{base}/v1/messages` (bearer +
  `anthropic-version`). The result is a property of the **backend, not the key**,
  so one pass applies to every provider row of that source.
- **`sync_source`** is the engine + dedup against [[model_probe_ledger]]:
  - **new** model → probe every protocol (the only calls, normally);
  - **seen + passed** → trusted, never re-probed (the bulk → cheap daily runs);
  - **seen + failed** → re-probed (it can flip when the backend adds support);
  - **gone from catalog** → dropped (overwrite semantics).
  Returns the passing per-protocol lists and persists the ledger.
- **CLI** (`python -m …model_sync`): refreshes the committed ledger for any
  source whose key is in env (`NETMIND_API_KEY` / `OPENROUTER_API_KEY` /
  `YUNWU_API_KEY`). Used by the release pipeline (ship a fresh ledger) and the
  cloud daily 05:00 job.
- **`apply_ledger_to_db(db)`**: overwrites `user_providers.models` for EVERY row
  of the in-scope sources from the ledger's pass-lists — one bulk, dialect-safe
  `db.update` per (source, protocol). Called by [[model_sync_runner]] after a
  probe pass. system_pool rows are overwritten from the netmind entry.

## Upstream / downstream

- Reads/writes [[model_probe_ledger]] (the committed JSON dedup cache).
- The manual "Update models" button → `backend/routes/providers.py:sync_default_models`
  calls `sync_source` per in-scope source with the user's key, then overwrites
  `user_providers.models`. [[model_catalog]]`.get_default_models` reads the ledger
  (authoritative once populated; falls back to the hardcoded `_DEFAULT_MODELS`).

## Gotchas

- Concurrency-capped probes (`_PROBE_CONCURRENCY`) so the initial 86-probe seed
  doesn't hammer upstream; steady state is a handful.
- `system_pool` has no separate probe — it reuses the `netmind` ledger entry.
