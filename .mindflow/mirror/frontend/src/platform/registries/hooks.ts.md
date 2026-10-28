---
code_file: frontend/src/platform/registries/hooks.ts
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `useRegistryEntries`：组件订阅注册表

`useSyncExternalStore(subscribe, snapshot)`。只有 Sidebar / SettingsPage / BookmarkPanelHost / App
用它；读到的数组引用在注册表不变时稳定。
