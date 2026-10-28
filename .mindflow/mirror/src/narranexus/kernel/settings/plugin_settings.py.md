---
code_file: src/narranexus/kernel/settings/plugin_settings.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a.4）— `PluginSettings`：env > 存储行 > schema 默认

内核只拥有优先级规则与类型化（`SettingField.coerce`）；行存哪里由宿主注入的 `SettingsStore` 决定
（平台给 `DbSettingsStore`，测试与最小宿主用 `MemorySettingsStore`）。读是同步的（热路径不 await），
写经 store 并刷新快照。未声明的 key 直接 `KeyError`（插件读不到自己没声明的设置）；`required` 且未设
也 fail-loud；`snapshot()` 默认打码 secret。
