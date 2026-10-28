---
code_file: frontend/src/platform/registries/settingsSections.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（预审修订）— `sortedSettingsSections(opts, entries?)`

与侧栏同型：过滤（desktopOnly/cloudHidden）+ 排序只此一份，`SettingsPage` 传入订阅快照。

## 2026-09-03 — 设置页分区注册表

左侧导航项与右侧面板从这里来；`desktopOnly`/`cloudHidden`/`neverDefault` 把原来的三条可见性规则
变成数据，插件分区自动受同样的门。`sortedSettingsSections` 是唯一的过滤+排序入口。
面板组件接 `{ navigate }` 做跨分区跳转（模型默认页跳 providers/plugins）。
