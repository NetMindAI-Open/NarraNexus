---
code_file: packages/narranexus-contracts/src/narranexus/contracts/distribution.py
last_verified: 2026-09-07
stub: false
---

# contracts/distribution.py — narranexus-dist.json

The distribution contract (spec section 19.3): `DistributionSpec` (id, displayName, engine range, `plugins` id → `PluginRef` {range | path}, excludes, branding, `auth`, defaults, `runtime` {userPlugins, deployment}, targets, bindings). `parse_distribution()` normalises plugin refs (a string is a range, an object with `path` is bundled) and rejects: bad ids, excludes overlapping plugins, cloud deployment with userPlugins, `bindings[kernel.auth]` disagreeing with `auth`, unknown fields. `DistributionError` is the single error type. Parsing only; resolution is in the kernel.

## 2026-09-07 — PLUGIN_ID_RE is the one plugin-id grammar

Defined here (the leaf every layer may import): the kernel's manifest validation and the index validator use it; DISTRIBUTION_ID_RE is the same expression. Words joined by single separators only, so the flattened id is an injective table/env prefix.

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
