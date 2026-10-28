---
code_file: plugins/builtin.channels.lark/src/narranexus_plugins/lark_module/_lark_credential_manager.py
last_verified: 2026-09-04
stub: false
---
## 2026-08-14 — `update_workspace_path` 删除（死代码）

lark_cli_client 的懒迁移写改走 seam `patch_credential` 后，`update_workspace_path` 全仓零调用者，按铁律 #8/#2 删除。apply_patch docstring 里"不相交列并发写者"的举证同步改为只引 `update_auth_status`（lark_trigger 仍在调、仍是真实的单列写者）——**按列写的设计本身保留**，被删的只是引证。

## 2026-08-11 (二轮审查) — apply_patch 只写被 patch 的列（收窄丢更新窗口）

原 apply_patch 是**整行** read-modify-write（save_credential 写全部列），会 clobber 并发的单列写者（trigger 的 `update_auth_status(BRAND_MISMATCH)`、懒 `update_workspace_path`）——它们写不相交的列，却被整行回写盖掉。改为：deep_merge 后经 `_cred_to_columns`（新静态方法，列序列化单一来源，save_credential 也复用）算出全列，再按 `_PATCHABLE_COLUMN`（raw-key→列名，app_secret_encoded→app_secret_encrypted）**只 update patch 指名的列**。不相交列不再被盖；同列并发仍需锁（三击 per-key 写罕见重叠）。未知字段→ValueError。守卫测试 [[test_lark_apply_patch]]（只写指名列 + is_active 0/1 + 拒未知字段）。

## 2026-08-11 (审查收口) — 删除被 apply_patch 取代的 3 个死写方法

`patch_permission_state` / `set_app_secret_encoded` / `update_app_credentials` 已**删除**（铁律 #8/#2）——`apply_patch` 是唯一写路径，全仓零调用者。留着危险不在体积：那三个是**按列 UPDATE**、`apply_patch` 是**整行 read-modify-write**，语义不同，并存会让下个改 lark 的人挑错 API。`update_auth_status`/`update_bot_identity`/`set_is_active` 仍有 backend 路由 + [[lark_trigger]] 调用，保留。`apply_patch` docstring 记下**整行写 vs 单列写的丢更新窗口**（trigger 的 update_auth_status 罕见路径），当前 setup 用户步进+每 agent 串行故可容忍。

## 2026-08-11 (lark 原语) — apply_patch / save_raw（seam 写接口）

`apply_patch(agent_id, patch)`=读→`to_raw_dict`→`deep_merge`→`_cred_from_raw`→`save_credential`(复用现有序列化+列映射 app_secret_encoded↔encrypted/permission_state↔json)，是唯一写路径（取代已删的 patch_permission_state/set_app_secret_encoded/update_app_credentials）；`save_raw`=PUT，**agent_id 从 path 钉死**防改包重定向别人。缺凭据 patch→清晰 ValueError。

## 2026-08-11 (PR-F) — 原始视图 + 反序列化助手（channel seam 接入）

新增**显式** `to_raw_dict()`（全字段含密钥 app_secret_encoded + permission_state 原样 + 时间戳 datetime→ISO/str 透传，因 Lark 无 to_public_dict 且 created_at 不经 _parse_dt）+ `_cred_from_raw(raw)` 逆转（ISO 串解析回 datetime）。供 [[channel_store]] seam（读方法登记为 `get_credential`，与其它 channel 的 `get` 不同）+ owner-gated 端点 [[channel_credentials]]。


## 2026-07-29 — bot_open_id + update_bot_identity

Added `bot_open_id`, sourced from `/open-apis/bot/v3/info` alongside
`bot_name`. It exists for [[lark_trigger]]'s group @-mention gate: Lark's
mention payload carries open_ids, and matching on display name alone is
wrong the moment a human shares the bot's name.

`update_bot_name` became `update_bot_identity(agent_id, bot_name,
bot_open_id)` — both fields come from one response, and each is written
only when non-empty so a partial response can never blank a good stored
value. All three call sites (`do_bind` in [[_lark_service]],
`_finalize_setup` in [[_lark_mcp_tools]], and the backend's
`/api/lark/auth/complete` route) now go through it.

Existing rows keep an empty `bot_open_id` until the next bind — the gate's
name fallback covers them, which is why that fallback is not dead code.
## 2026-07-13 — set_is_active (activation without re-bind)

Added `set_is_active(agent_id, is_active)`, mirroring the other channels' `set_enabled`. Flipping `is_active` → True is what makes the trigger's credential watcher pick up a bundle-imported (inactive) Lark credential and claim the app's single WS slot. Called by `POST /api/lark/set-active`.

# _lark_credential_manager.py — the Lark credential manager (persisted in channel_credentials)

## Why it exists

Per-agent Lark/Feishu bot binding state. Holds App ID + Secret
reference (Secret itself lives both in lark-cli's Keychain for CLI
tools, AND base64-encoded in DB for the SDK trigger that can't read
the Keychain). Centralises auth_status state-machine.

## auth_status state machine

```
not_logged_in   — DB row exists but credentials never verified
bot_ready       — `auth status` succeeded; trigger can subscribe
user_logged_in  — bot_ready + user OAuth complete (search features)
expired         — credential validation failed; needs re-bind
brand_mismatch  — runtime-detected (WS error 1000040351) wrong platform
```

`AUTH_STATUSES_BOT_ACTIVE = {bot_ready, user_logged_in}` is the
allowlist the trigger watcher uses to decide whether to start /
restart a subscriber for a credential. **brand_mismatch is excluded
intentionally** — restarting the trigger only re-hits the domain
error in a hot loop. The user has to unbind + re-bind with the
correct brand to recover.

## 2026-05-27 — added AUTH_STATUS_BRAND_MISMATCH (B.1)

Detected at runtime by `lark_trigger` when the WebSocket subscriber
observes error `1000040351`. State stored so:
- The trigger watcher won't keep restarting the doomed subscriber.
- The frontend can render a clear "wrong platform — re-bind" card
  ([[LarkConfig]] State 5).
- The agent prompt knows about it ([[lark_module]] Auth guidance)
  and can tell users "you picked the wrong platform" when they
  complain about silent bot.

## Gotchas

- `app_secret_encoded` is base64, **not encryption**. Inline comment
  flags this; production env should plug in cryptography.fernet via
  `LARK_SECRET_ENCRYPTION_KEY`.
- `migrate_legacy_auth_status` is the one-shot migrator for pre-4-
  state DB rows (`logged_in` → `bot_ready`). Conservative downgrade
  — we can't tell from the old row whether user OAuth was completed.

## 2026-09-04 · persistence switched to the generic store (batch 4d.2)

`LarkCredential` keeps its shape (the SDK / route / trigger contract) but the manager stores it through `GenericCredentialStore` under channel `lark`: `app_secret_encoded` (base64, the SDK's working form) + `app_secret_ref` are the secret half — encrypted by the store — everything else including `permission_state` is public, `is_active` ↔ the store's `enabled`, `app_id` is the channel-wide `external_id` (two agents can no longer bind the same Lark app, which is exactly the single-WS-slot rule). `apply_patch` / `update_auth_status` / `set_is_active` go through `store.patch` (version-checked field merge), so the trigger's status write and the panel's permission patch never clobber each other; `_PATCHABLE_FIELDS` still fails loud on unknown names and `save_raw` still pins the path's agent_id. `lark_credentials` is retired (copied in by [[credential_legacy]]), never dropped.
