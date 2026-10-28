---
code_file: src/narranexus/platform/channel/credential_legacy.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 新增 `purge_legacy_for_agent`：与发行版无关的一遍清扫

`LEGACY_TABLES` 本来就是「哪些表曾经存过渠道密钥」的唯一真值表，所以删除 agent 的清理没
有理由再绕一圈描述符/已安装插件。`purge_legacy_for_agent(db, agent_id, channel=None)` 只
按 `agent_id` 删，`channel=` 可把范围收到一个渠道（`ChannelModuleBase` 这么用）。

它存在的原因是两个都会把 base64 bot token / app secret 永久留在库里的洞：

- `cleanup_for_agent` 的提前返回（见 `channel_module_base`）；
- 清理遍历只访问**已注册**的渠道模块，而两个示例发行版都排除了全部六个渠道——「删除我
  的 agent」对这些部署等于不删凭据。

按**表**尽力而为：从来没有这张表的安装不能因此让删除失败（`debug` 一行，继续下一张）。
返回 `{表: 删除行数}`，只包含真的删掉了东西的表，方便调用方并进 stats 而不会凭空多键。

# channel/credential_legacy.py — the retired per-channel tables, described once

## Intent

Batch 4d switched every builtin channel manager onto `channel_credentials`. The six historical tables (`channel_{telegram,slack,discord,wechat,narramessenger}_credentials`, `lark_credentials`) are retired but never dropped (rule #6), and two consumers still need to understand their rows: the one-shot copy migration (`m0004`, re-run as `m0005`) and the bundle importer when it meets a bundle exported before the switch. `LEGACY_TABLES` is the single description of that shape — table name, channel, the enabled column (`is_active` for lark, `enabled` elsewhere), the base64 `*_encoded` / `_encrypted` secret columns and how they decode into the generic values — so the two consumers cannot drift.

## Design decisions

- **Decode at the boundary.** Legacy rows stored secrets base64-encoded; the generic store encrypts. `LegacyTable.to_values(row)` returns plain values (plus the active flag) (lark keeps its base64 `app_secret_encoded`, the SDK's working form) and lets the store encrypt.
- **Copy, never move.** `copy_legacy_tables` inserts only rows whose (channel, agent) is absent from the generic table — the switch is idempotent and a generic row that was already edited wins. A missing legacy table (fresh install) copies zero rows.
- **Enabled travels.** The copy preserves the active flag; import-time force-inactive is the bundle importer's rule, not this module's.

## Consumers

`backend/migrations/m0004` + `m0005` (`copy_legacy_tables`), `bundle/channel_credential_tables.py` (`legacy_rows_to_generic` for pre-4d bundles), `tests/channel/test_generic_credential_store.py::test_legacy_tables_are_copied_once_with_secrets_decoded`.
