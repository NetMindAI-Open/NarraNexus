---
code_file: plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/awareness.py
last_verified: 2026-09-07
stub: false
---

# nexus_plugins_module — awareness.py

The agent's self-awareness: `platform_overview()` (host version, mode, distribution, builtin + user plugins, slot domains, non-default bindings), `platform_slots(domain)` (the catalog), `contract_docs(kind)` (contract classes with docstrings/members + slots), `agent_self(db, agent, user)` (capabilities, model slots, prompt sections in effect), `capability_set(...)` (owner-only, refuses base capabilities, persists via CapabilityService).

## 2026-09-07 — capability_set takes the injected identity; agent_self hides the owner id

capability_set's user_id is the caller identity the MCP layer injects, never a tool argument (the model could type the id agent_self used to hand it); absent identity is refused. agent_self returns is_owner only.

## 2026-09-07 — platform_overview lists the parsed manifests

builtin_plugins come from builtin_manifests() (Manifest objects), not from a kernel dict.

## 2026-09-07 — 按 kind 找槽改用 SlotTree.by_kind（B7）

SLOT_KINDS 已删。
