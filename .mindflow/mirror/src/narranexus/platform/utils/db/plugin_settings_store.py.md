---
code_file: src/narranexus/platform/utils/db/plugin_settings_store.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2a.4）— 数据库实现的 `SettingsStore`

内核 `PluginSettings` 是同步快照式读取，这里把它桥到异步 `AsyncDatabaseClient`：无运行中 loop 就
`asyncio.run`，在 loop 线程里被调则丢到工作线程私有 loop（不阻塞事件循环）。secret 用平台 `SecretBox`
（Fernet）加密后落 `value_json`、`is_secret=1`，读出时解密——库转储里永远没有明文插件凭据。
放在 `utils/db` 而不是内核，因为内核禁止 import `xyz_agent_context`（secret_box 在市场实现包里）。

Batch 6 fix: without an injected client the store runs every operation on a private long-lived loop thread (`_PrivateLoop`) with that loop's own `get_db_client()`, so the sync `SettingsStore` protocol works from the host event-loop thread (plugin activation) and from plain sync code without touching the host's loop-bound client; an injected client keeps the previous `_run` behaviour.

## 2026-09-07 — updated_at refreshed on update

save_async writes updated_at on the update path; the column existed but only ever held the insert time.
