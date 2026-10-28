---
code_file: frontend/src/components/bookmarks/builtinTabIds.ts
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 - Authored IDs are separate from lifecycle ownership

The literal list includes Browser so shell entry points remain type checked.
Browser is owned by `builtin.browser`, not `builtin.ui`; the registration test
checks the union of shell-owned strip tabs and the browser feature's strip tabs.
The historical shell-only ownership assertion below is superseded.

## 2026-09-23 浏览器支持

增加 browser 活动面板字面量，使聊天入口、抽屉请求与注册项共享编译期检查。

# builtinTabIds.ts — 壳自有 rail tab id 词表

## 2026-09-07（批 1 三轮复审移植）— 新建

条带从 `PANELS` 注册表派生后，`AtomicTabId` 放开成 `string`，壳自己手写的 id（`platform/builtin.ts` 的注册、
ChatHeader 的固定分组）失去了编译期校验——拼错一个 id 是运行时空面板。本文件是那份唯一的字面清单 + 类型；
`builtin.test.ts` 钉住「注册表里 owner=builtin.ui 且带 strip 的 tab」恰好等于这份清单，两边不会漂。
