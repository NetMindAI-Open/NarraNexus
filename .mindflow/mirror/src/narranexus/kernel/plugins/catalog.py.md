---
code_file: src/narranexus/kernel/plugins/catalog.py
last_verified: 2026-09-07
stub: false
---

# kernel/plugins/catalog.py — the slot catalog

`slot_catalog(registries, domain=None)`: every slot grouped by domain (`DOMAINS` order: kernel, prompt, turn, model, agent, ingress, backend, content, ui, builtin) with contract, arity, default, distribution-only flag, candidates (owners), contributions and the live binding (provider + layer/origin, or ordered providers). `toml_template()` renders a commented `narranexus.toml`. Used by `narranexus slots`, the docs generator, `GET /api/plugin-factory/slots` and the agent's awareness tools.

## 2026-09-07 — toml_template comments every line

The dead ternary (both branches '# ') is one assignment with the rule stated: the template documents, the user uncomments what to change.

## 2026-09-07 — no 'builtin' domain

The NexusPower seats live under turn.pipeline.act.framework.nexus_power.*, so the catalog's orphan 'Framework-owned sub-slots' domain is gone.

## 2026-09-07 — DOMAINS 表删除，域来自树的根（B7）

domains(tree) 返回 (domain, title)：树根的声明序与 doc；第三方插件自己的命名空间根自然排在内核根之后。
