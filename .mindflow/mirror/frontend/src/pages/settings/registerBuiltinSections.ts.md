---
code_file: frontend/src/pages/settings/registerBuiltinSections.ts
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 浏览器支持

注册 BrowserSection，让工具返回的 settings/browser 安装入口在整页设置中确实可达。

BrowserSection is owned by `builtin.browser`, not `builtin.ui`. The shared
registry's disabled-owner guard also rejects this registration if the settings
chunk is first loaded after the plugin was disabled. Removal hides the full-page
settings entry, and a disabled `?tab=browser` link falls back to providers.

## 2026-09-03 — 在设置 chunk 内注册内置分区

`SettingsPage.tsx` 顶部 side-effect import。放在这里而不是 `platform/builtin.ts` 的原因：让分区组件留在
lazy 的设置 chunk 里并同步渲染，bundle 形状与页面行为与改造前一致。注册**无条件**：模块被二次求值
是 bug，注册表要用 `RegistryConflictError` 把它暴露出来，不用 `has()` 兜底（预审指出那是 fail-open）。
唯一合法的二次求值是 Vite HMR 替换本模块——`import.meta.hot.dispose` 先撤销上一轮注册。
ES 模块在一次页面/测试文件生命周期内只求值一次，所以测试 setup 与页面同时 import 不会撞。
顺序值 10..80 对应原 `NAV_ITEMS` 顺序。
