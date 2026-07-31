---
code_file: src/xyz_agent_context/agent_framework/providers/model_sync.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — PASS 不再终身信任（TTL 复测 + 三态判定）

老语义"pass 过一次就永远信任"是脏列表的根源：模型还挂在上游 catalog 页但推理
后端早死了，它就永远留在所有用户下拉框里（committed ledger 是 6/24 的，之后
没人复测过）。现在：

- probe 返回三态 verdict：`ok`(200) / `model_error`(400/404/422，确定性拒绝) /
  `transient`(429/5xx/auth/billing/网络)。
- PASS 条目 `tested_at` 超过 `_REPROBE_TTL`(7d) 进复测队列，每轮 `_REVALIDATE_CAP`
  条、最旧优先；**只有 `model_error` 翻 FAIL**，`transient` 保持 PASS 且不刷新
  时钟（下轮重试）。`tested_at` 是按模型共享的——本轮该模型任一协议 transient
  就整体不刷新时钟，否则另一协议的重试会被吞掉。
- **防误杀护栏**：一轮复测零 OK 且 ≥`_MASS_FLIP_GUARD_MIN` 个确定性失败 = key/
  后端全局故障（余额耗尽的 key 会让所有调用 400），整轮不翻转只报 error——否则
  一夜清空全部用户的下拉框。402 刻意不算 model_error，同一原因。
- catalog 拉回空 dict 直接 raise：那是上游 API 变形/故障，不是"全部模型下架"，
  照旧走 overwrite 会把 ledger 清光。
- `sync_source(suspects=...)`：运行时上报的 (model, protocol) 嫌疑对无视 TTL
  立即复测（见 [[model_health]]），确定性失败当场翻 FAIL、当日移出列表。

## 2026-07-30 — 免费卡 = 网关目录 ∩ 探测判定；网关独有模型入探测并集

三个写入方（每日 job / provision 的 `get_default_models` / Update-models 按钮）
曾各写各的 free 名单（裸网关 61 / netmind 通过名单 / 追加目录默认），互相覆盖。
现在收敛到一份数据：

- `sync_source(extra_models=...)`：runner 把网关 served 列表并进探测宇宙。
  网关独有 id（doubao、R1-Turbo 等公开目录不列但后端仍在服务的）照常探测、
  照常 TTL 复测，但 ledger 里带 `extra` 标记——**永不进 netmind 卡列表**，只
  服务 free 卡的门。目录收录某模型时 extra 标记自动脱落。`extra_models=None`
  （按钮路径不知道网关列表）= 保留现有 extra 条目，不当"已下架"清除。
- `apply_free_tier_gate(ledger, gateway_models)`：按协议生成 free 名单——
  网关模型除非 netmind 判定 FAIL 否则收录（**未知判定=收录**：网关今天在路由
  它，probe 断供绝不能清空下拉；并集机制下下一趟就会探测到）。结果同时写成
  ledger 的 `netmind_free` source 条目，`get_default_models("netmind_free")`
  从它取种子（**本地回落用**——云上 backend 与 model-sync 是两个容器,文件
  ledger 不互通）;云上的另两个写入方各自走 DB:provisioner 在 seed 时
  `load_ledger_db`+过门传按协议 dict,Update-models 按钮对 netmind_free 行
  从 DB ledger 的 netmind_free 条目**覆写**(条目缺失=门没跑过,不动卡)。
- `compute_drift`：`gateway_failing`（网关配着但**全协议** FAIL 的——单协议缺位是 OSS 模型常态,算漂移会让告警恒亮=等于关掉告警）+
  `catalog_pass_not_in_gateway`（目录通过但网关没配的——加不加是定价决策，
  永不自动化）。transient 天生不可见（判定只在确定性错误上翻转）。

`apply_ledger_to_db` 仍然不碰 `netmind_free` 行；卡的覆写走
`refresh_free_tier_models(db, ledger=)`（网关列表过门后按协议写入）。

## 2026-07-28 — 免费卡的目录不走 ledger（原始设计，上节是其收敛版）

免费额度经我们自己的网关打到 NetMind，而网关只路由（也只定价）配置里的模型
—— 把上游全目录盖上去，等于在下拉框里放一堆一调就 400 的选项。

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

## 2026-07-30 (review 修补,PR #201 反馈)

- `extra` 不变式收敛到唯一读取口 `model_probe_ledger.passing_models`:
  `apply_ledger_to_db`/`ledger_models`/`res.lists` 全走它——此前 apply 不过滤,
  网关独有模型会灌进所有 netmind/system_pool 付费卡(用户自己的 key 未必被
  授权/计价)。
- 新模型探测 TRANSIENT **不落 verdict**(缺失=未知,free 门收录,下一趟按
  「FAIL 或缺失」规则重探)——此前 transient 落 FAIL,一次抖动就让 free 下拉
  少一片一整天,与「probe 断供不清空下拉」不变式矛盾。
- extras 的裸 id meta 只 setdefault,不冲掉既有 display_name。
- runner 生命周期:started/每 pass heartbeat(带 summary+drift)/stopped 进
  service_audit(L2);drift 不再写 error 行。
