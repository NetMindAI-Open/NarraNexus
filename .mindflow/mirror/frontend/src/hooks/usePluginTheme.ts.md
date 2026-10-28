---
code_file: frontend/src/hooks/usePluginTheme.ts
last_verified: 2026-09-08
stub: false
---

# usePluginTheme.ts — 把选中的插件主题应用到根元素

## 2026-09-08 — 新建

同时订阅 `themeStore.pluginTheme` 与 `THEMES` 注册表：主题 id 是持久化的选择，token 却要等插件激活后才注册（可能远晚于首帧），
所以「选择存在且已注册」才 `applyTheme`，否则 `clearTheme`——插件卸载或用户改回默认都会正确清掉。挂在 `App` 里，全局只一处。
