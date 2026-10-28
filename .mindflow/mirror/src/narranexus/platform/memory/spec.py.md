---
code_file: src/narranexus/platform/memory/spec.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03 — 注册表来自内核门面；`contribution_for(kind)` 供内置 manifest 引用

`MEMORY_KIND_REGISTRY = KERNEL_REGISTRIES.registry_for("agent.capabilities.memory_kinds")`；
`register_spec` 生成并缓存 `Contribution(kind, lambda: spec)`，`specs.py` 用 `contribution_for`
拼出 `CONTRIBUTIONS` 元组给 `builtin.memory_kinds` manifest。

## 2026-09-03 — kind 注册表改用内核 `Registry`（`MEMORY_KIND_REGISTRY`）

`_REGISTRY: Dict` → `narranexus.kernel.plugins.registry.Registry`，`register_spec` 用
`replace=True` 保持「re-import 幂等」，`get_spec` 的 KeyError 文案原样保留，`all_kinds` 顺序 =
注册顺序（与之前 dict 插入序一致，快照 `registries.json` 钉住）。`register_spec` 多一个
keyword-only `owner`（默认 `builtin.memory_kinds`）。契约层 `narranexus.contracts.memory.
MemoryKindContract` 只要求 `kind`/`passive`，六个内置 spec 满足（契约测试）。

## 2026-06-08 — `passive` flag splits the two recall surfaces

Added `passive: bool` to MemoryKindSpec plus a `passive_kinds()` helper. Passive kinds (observation/entity/narrative) are the distilled knowledge auto-injected EVERY turn by GeneralMemory's hook; the `remember` / `grep_memory` TOOLS instead span ALL searchable kinds (incl. interaction/job/bus). This split is the mechanism behind removing chat-echo pollution from passive injection while keeping everything explicitly searchable. See [[specs]] for the per-kind flags.

# spec.py — unified Agent Memory

MemoryKindSpec (per-kind policy: dedup_key, merge, prompts, recall weights, render, evict) + the kind registry. The 'policy' half of mechanism-vs-policy.

Part of the unified memory system (`refactor/agent-memory`). The unified design covers data model, retrieval stack and migration
(author-local; the § numbers below cite its sections). Mechanism vs policy split
(§3): the Engine holds the fixed lifecycle algorithm; each kind's Spec holds
policy. No vectors — recall is BM25 + grep + structured filters.

## 2026-09-04 · lazy builtin kinds (batch 6b.2)

`ensure_builtin_kinds()` registers the manifest-named kinds on the first `get_spec` / `all_kinds` / `passive_kinds` — lazy so importing the plugin package first cannot recurse into a half-initialised module; the plugin's import-time `register_spec` registers the same objects, so either order yields one registry.

## 2026-09-07 — declare_spec vs register_spec; memory_kind_registry() at call time

declare_spec(spec) makes a kind a Contribution a manifest can name without touching a registry (what builtin.memory_kinds does at import); register_spec(spec, owner=) is the explicit registration for tests / hosts. ensure_builtin_kinds is gone.
