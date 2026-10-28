---
code_file: frontend/src/pages/settings/sections.tsx
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 浏览器支持

BrowserSection 复用 BrowserSettings，整页设置和弹窗共用相同运行时安装行为。

## 2026-09-03（批 2d.3）— `PluginsSection` 追加 `<PluginFactory />`

## 2026-09-03 — 设置页的内置分区组件（从 SettingsPage 拆出）

每个左导航项一个组件（Account/Providers/ModelDefaults/Plugins/Artifacts/Privacy/Personalization/
Updates），`SectionHeader`/`UpdatesSection`/`ProvidersSection`/`ArtifactsContent` 逐字搬来，
原 `SettingsPage` 里 Account 与 ModelDefaults 两段内联 JSX 收成 `AccountSection`/`ModelDefaultsSection`；`ModelDefaultsSection` 通过 `navigate` prop 跳转
（云端不给 plugins 跳转，保持原来「纯文本提示」的行为）。
