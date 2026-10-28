---
code_file: src/narranexus/platform/module_system/data_access/factory.py
stub: false
last_verified: 2026-08-11
---

## 2026-08-11 (PR-A) — 加 get_channel_credential_store

同一组合根多出 `get_channel_credential_store()`，与 `get_agent_data_store` 完全同构：同一 `NARRANEXUS_BACKEND_URL`
env 门（unset→[[channel_store]] DirectStore、set→ChannelHttpStore），同一 `current_identity_headers()` 转发。
两条 seam 共用 env 开关与身份转发，但各自独立的 Protocol/实现。

## Why it exists

Composition root for AgentDataStore. Picks transport by `NARRANEXUS_BACKEND_URL`
the same way `db_factory` picks a db backend and `broker_client` gates on
`BROKER_URL`: unset → DirectStore (current behaviour, no-op change), set →
HttpStore. One env var = no scattered `if is_cloud` in tools (rule #9/#20).

`current_identity_headers()` forwards the live MCP request's identity headers
(X-NarraNexus-* / borrowed bearer, read from `_mcp_identity._ambient_headers`)
to the backend on the Http path; empty with no ambient request. Until P2 flips
cloud over by SETTING the env var, every caller gets DirectStore — so landing
this is behaviour-preserving everywhere.

## Deployment-order contract (pre-review I3)

Setting `NARRANEXUS_BACKEND_URL` is only valid AFTER the identity chain is
provisioned (broker signing key + backend `NX_IDENTITY_PUBLIC_KEY_FILE`):
backend's nx-agent service path fails CLOSED, so flipping the env first turns
every Http call into a 401 the store surfaces as an in-band error string.
Flip the env LAST. The header forward is a strict whitelist (x-narranexus-* +
authorization) — cookies / x-forwarded-* never cross (test-pinned).
