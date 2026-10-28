---
code_file: frontend/src/platform/__tests__/builtin.test.ts
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

钉住 `browser_look` 渲染器归 `builtin.browser`，且 accepts 只收能解析的输出。组件是 lazy 包装，所以断言
accepts 行为而非组件引用；端到端渲染见 turnTimelineToolRenderer.test.tsx。

# Builtin Registration Contracts

Pins the shell's route table, navigation order, panel component identities and
settings visibility against accidental registration drift. Shell-authored panel
IDs include Browser, but lifecycle ownership belongs to `builtin.browser`; the
literal ID contract therefore includes both shell-owned panels and that feature's
panel. Plugin disable behavior is covered separately by `disableBuiltinUi.test.ts`.
