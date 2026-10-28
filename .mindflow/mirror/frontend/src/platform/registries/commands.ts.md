---
code_file: frontend/src/platform/registries/commands.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2d）— 命令注册表

⌘K 面板在壳自身命令之后列出注册表条目（label 可为 i18n key，`visible` 谓词渲染时求值）。加载器为 manifest
声明的命令登记 gate（先激活再执行插件替换后的命令）。

## 2026-09-07 — `when` renamed to `visible` (M-3)

The visibility predicate was named `when`, which collided in name (though not in mechanism) with
`registries/when.ts`'s `WhenClause` string grammar — a completely different system: `WhenClause`
strings are parsed and validated AT REGISTRATION time; this field is an arbitrary function called
directly at RENDER time in `CommandPalette.tsx`. Renamed to `visible` to match
`SidebarItemDef.visible` (`registries/sidebar.ts`), the closest existing precedent for "a
predicate function gates this entry's visibility". `CommandPalette.tsx` now also wraps the call
in a `try/catch` — a throwing predicate used to crash the whole ⌘K palette via `useMemo`;
it is now reported (`reportUiError`) and that one command is hidden instead.
