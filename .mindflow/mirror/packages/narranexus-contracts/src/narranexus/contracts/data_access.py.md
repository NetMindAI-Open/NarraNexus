---
code_file: packages/narranexus-contracts/src/narranexus/contracts/data_access.py
last_verified: 2026-09-04
stub: false
---

# contracts/data_access.py — AgentDataStore method bodies as plugin contributions

## Intent

`DirectStore` is the seam every MCP tool talks to locally, and until batch 3c.4 it imported five builtin modules to implement its methods. `DataAccessSpec(name, handler)` lets the plugin that owns a capability provide the body of the store method of the same name (`handler(db, *args)`), so the store becomes a dispatcher that keeps only platform policy: parity rejects/clamps mirrored from the routes, the never-raise invariant, the exact failure shapes. A disabled builtin simply has no provider and the tool gets its own failure dict saying so.

## Consumers

`module/data_access/store.DirectStore._handler`; providers in `module/<x>_module/data_access.py` (awareness, social_network, basic_info, job, chat) named by the builtin manifests and by `module/contributions.DATA_ACCESS_SPECS`.
