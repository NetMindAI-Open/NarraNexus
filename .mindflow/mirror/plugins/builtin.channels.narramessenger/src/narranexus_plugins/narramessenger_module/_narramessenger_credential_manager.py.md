---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_narramessenger_credential_manager.py
stub: false
last_verified: 2026-10-14
---

## 2026-10-14 — 游标写入不加任何「已禁用就跳过」的检查

迟到的游标写入（禁用后订阅者最晚一个轮询周期才停）会落在已禁用的行上，但那个值会在「禁用→启用」迁移时被
`reset_on_reenable` 清掉（见 [[credential_store]]、[[descriptor.py]]），所以 `update_since_token` 保持单次窄 patch，
不为此多读一次库。

## 2026-09-09 — `disabled_reason` 公开字段 + `set_enabled(reason=)`（B-28 I1）

凭据 dataclass 新增 `disabled_reason`（默认 ""）进两个视图 / `_cred_from_raw`；`set_enabled(agent_id,
enabled, reason="")` 透传给通用 store：禁用时写原因、启用时清空。`_values()` 复用 raw 视图，所以 upsert
也会带上当前值（bind 服务新建的 dataclass 默认 ""）。

## Why it exists

`channel_narramessenger_credentials` 表的 CRUD + `NarramessengerCredential` 数据类。**两个密钥**：`bearer_token`（控制面）+ `matrix_access_token`（消息面 Matrix），均 base64 编码落库、读出即解码。`get(agent_id)` 读、`upsert(cred)` 整对象写、另有 `update_since_token`/`update_device_id`/`update_owner` 高频窄写。`to_public_dict` 脱敏（去两密钥，since_token 降成 bool）。

## 2026-08-11 (PR-E) — 原始视图 + 反序列化助手（channel seam 接入）

新增**显式** `to_raw_dict()`（**不能**用 `{**to_public_dict()}`，因脱敏视图有损：去两密钥、since_token 降成 bool）——含两密钥 + 完整 `matrix_since_token`，只交给 [[channel_store]] seam 的 owner-gated 端点 [[channel_credentials]] + send/CLI 工具。配 `_cred_from_raw(raw)` 逆转（全字段，datetime 经 ISO round-trip）。

## 2026-08-11 — two reverse lookups for the prewarm endpoint

`get_by_matrix_user_id` and `get_by_profile_id` were added. Both are needed
by a follow-up "prewarm" HTTP endpoint: NarraMessenger calls in with the
agent's Matrix identity (`@agent-…:homeserver`) today, and — in future —
its own `agent_profile_id`. Until now every query filtered by `agent_id`
only, so there was no way to resolve a credential row from either of
those. Both follow the existing `get()` shape (empty-string guard, decode
via `_row_to_cred`, `None` on miss); `get_by_matrix_user_id` relies on the
pre-existing UNIQUE index to guarantee at most one row, `get_by_profile_id`
does not (see caveat below).

**Caveat — `nexus_profile_id` back-fill gap**: the column existed in the
schema since inception but `do_bind` never wrote it (see
`_narramessenger_service.py.md` 2026-08-11 entry) — every row bound before
this change has `nexus_profile_id == ""`. `get_by_profile_id` only
resolves rows bound (or rebound) after 2026-08-11; older agents need a
rebind before the prewarm endpoint can find them by profile id. A
non-unique index (`idx_nm_cred_profile`, see `schema_registry.py`)
backs the lookup — non-unique because empty-string rows would otherwise
collide on a unique index, and profile-id uniqueness isn't a contract
we've verified against NarraMessenger's platform semantics.

## 2026-09-04 · persistence switched to the generic store (batch 4d.2)

Same move as the other four managers in 4d.1: reads and writes go through `GenericCredentialStore` under channel `narramessenger` (`_cred_from_raw(record.to_raw_dict())`), the token is the encrypted secret half, `enabled` maps 1:1. `channel_narramessenger_credentials` is retired (copied in by [[credential_legacy]]), never dropped.
