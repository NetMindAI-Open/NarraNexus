---
code_file: packages/narranexus-contracts/src/narranexus/contracts/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `API_VERSIONS` 的两类 key

注释写明：有注册表的 kind（`framework/provider/llm_client/memory/tool/...`，对应 `Registry.api_version`）与没有
注册表的契约模块版本（`agent/agent_events/services/ui/events`）共用一张表；`STABILITY` 覆盖两类。

## 2026-09-07 — `web` / `channel_authoring` 两个 alpha 条目（批 6c）

它们**不是** manifest/slot kind，而是「插件作者会 import 的表面」：
`contracts.web` 的 `WebHost`，以及 `narranexus.sdk` re-export 的那批 channel 基类。
放进 `API_VERSIONS` / `STABILITY` 的唯一理由是：griffe 门禁和 release notes 只读这两张表，
一个第三方赖以写代码、却没人给它定级的表面，正是 `docs/API_POLICY.md` §1 拒绝承担的
Hyrum's Law。两条都诚实地标 ALPHA（理由写在 API_POLICY §2），
为了让门禁好看而标 STABLE 等于承诺一个还兑现不了的废弃窗口。

## 2026-09-04（批 3c.1）— `API_VERSIONS["module"]`

## 2026-09-04（批 3a）— `API_VERSIONS` += stage_strategy / pipeline_profile / context_provider

## 2026-09-03（批 2a）— `API_VERSIONS` 新增十个平台/内容 kind

`hook/route/table/worker/settings/tool/mcp_server/bundle/skill/theme` 各自一个契约模块（theme 在 `ui.py`），
全部 alpha。`contributes` 白名单就是这张表的键（spec §5.1）。

## 2026-09-03（批 1）— `API_VERSIONS` 新增 `agent_events` / `agent` / `services` / `ui`；`Namespace`

事件字典契约独立计版本（线上协议），与 `framework`（driver Protocol）分开 bump；`agent`（纵横模型）、`services`（`kernel.*` 位的宿主服务 Protocol）、`ui`（前端壳描述）各自一个 kind，全部 alpha。`Namespace` 是分组位（`model`/`agent`/`backend`/`ingress`/`content`）的契约符号：内核拥有、永不绑定，存在只为让 `kernel/plugins/slots.py` 里每个契约字符串都能 import 到（`test_every_kernel_slot_contract_symbol_resolves`）。

## 2026-09-03 — 公开 API 面的唯一入口

插件只允许 `from narranexus.contracts import ...`（宪章 8/11）。本文件导出 `_base` 的原语
（Disposable/DisposableStack/CancellationSignal/错误层级/Stability）并持有两张表：
`API_VERSIONS[kind]`（每个契约 kind 的整数版本，只增不减，破坏性改动必须 bump）与
`STABILITY[symbol]`（alpha/beta/stable）。批 0-5 全部 alpha，批 6 才标 stable
（spec §5.5 / §16.1）。`tests/nx_kernel/contracts/test_base.py` 钉住两表键集一致且全 alpha；
`tests/nx_kernel/test_package_layout.py` 钉住 import 本包不会带进 kernel/legacy/backend。

## 2026-09-04 · ingress triggers (batch 3c.3)

`API_VERSIONS["trigger"] = 0` for `contracts/trigger.py`.

## 2026-09-04 · data-access providers (batch 3c.4)

`API_VERSIONS["data_access"] = 0`.

## 2026-09-04 · channels as descriptors (batch 4a)

`API_VERSIONS["channel"] = 0`.

Batch 6c: contract kind `auth` (the `kernel.auth` slot / authProviders, spec section 19.5) joins `API_VERSIONS`.

Batch 6d: `STABILITY` marks every kind STABLE (docs/API_POLICY.md governs changes; griffe checks the surface in CI); kind `auth` added in 6c.

2026-09-07: kind `prompt` (stable, like the rest).

## 2026-09-07 — MIN_SUPPORTED_VERSIONS; STABILITY declared per kind

MIN_SUPPORTED_VERSIONS is the oldest contract version a plugin may declare per kind (equal to API_VERSIONS until a kind is bumped; the previous version stays for the deprecation window). STABILITY is an explicit per-kind table rather than a comprehension over API_VERSIONS: marking a kind stable is a decision, and a new kind starts ALPHA until promoted here.
