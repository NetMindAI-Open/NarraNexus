---
code_file: plugins/builtin.channels.telegram/src/narranexus_plugins/telegram_module/_telegram_credential_manager.py
stub: false
last_verified: 2026-09-10
---

## 2026-09-09（复审 I1/I4）— 五频道同一口径

`set_enabled(*, reason=)`（keyword-only，与通用 store 同形，复审 M4）现在是 telegram/slack/discord/wechat/narramessenger 五个 manager 的共同形状，
底下的通用 store 在 patch 版本竞争丢失时返回 False（不再抛）；trigger 侧对 False 打 ERROR。

## 2026-09-09 — `disabled_reason` 公开字段 + `set_enabled(reason=)`（B-28）

`TelegramCredential.disabled_reason`（默认 ""）进 `to_public_dict` / `_cred_from_raw`；
`set_enabled(agent_id, enabled, reason="")` 透传给通用 store，禁用时写原因、启用时清空。
trigger 命中永久失败（401 token 撤销 / 409 别的 poller）时写入，面板与 `tg_status` 都能看到。

## Why it exists

`channel_telegram_credentials` 表的 CRUD + `TelegramCredential` 数据类。密钥（bot_token）落库前 base64 编码、读出即解码（明文字段只给调用方、绝不 log）。`get(agent_id)` 读、`to_public_dict` 是面板用的脱敏视图（无密钥）。

## 2026-08-11 (PR-B..D) — 原始视图 + 反序列化助手（channel seam 接入）

新增 `to_raw_dict()`（含明文 bot_token，= `{**to_public_dict(), 密钥}`，与脱敏视图严格区分，只交给 [[channel_store]] seam 的 owner-gated 端点 [[channel_credentials]] 与 send 工具）+ 模块级 `_cred_from_raw(raw)` 逆转（datetime 经 ISO round-trip，复用 `_parse_dt`），供 HttpStore 重建与 DirectStore 相同的 dataclass 保 parity。

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

Persistence moved to the generic `channel_credentials` table through `GenericCredentialStore` (clean field names; the secret is encrypted by the store, so the per-channel base64 helpers and `_row_to_cred` are gone — `_cred_from_raw` rebuilds the dataclass from `to_raw_dict()` shapes). The public API (`bind` validation with the platform SDK, `get` / `get_public` / `unbind` / `set_enabled` / `list_active`, the identity/owner updates) is unchanged; `update_owner`'s first-DM compare-and-set is `update_if(owner_user_id == "")`. `TABLE` names the retired bespoke table for the m0004 backfill and diagnostics only.
