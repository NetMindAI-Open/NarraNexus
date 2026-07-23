---
code_file: src/xyz_agent_context/repository/_team_marketplace_seed.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — review 修复:同步 httpx→异步

`httpx.Client`(同步,在 async 里冻 loop)改 `httpx.AsyncClient` + await。


# _team_marketplace_seed.py

Bootstrap seed: 9 official team templates ported from the unmerged ee1db871
catalog. Diverges on hosting — instead of pointing at narra.nexus, it
FETCHES each `.nxbundle` once from the narra.nexus source URL (migration
input only), verifies sha256, stores it in OUR template store, and writes a
catalog row with the resulting store_key. Idempotent (skips re-upload when
the store already has the key), best-effort per-entry (one unreachable
source never aborts the rest). Runs in the backend lifespan only where the
instance IS the registry (cloud / SKILL_MARKETPLACE_LOCAL_REGISTRY).
