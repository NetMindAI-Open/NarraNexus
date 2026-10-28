---
code_file: src/narranexus/kernel/plugins/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 插件运行时原语（不做 re-export）

`registry`（one 位的提供者表）/ `hooks`（many 位，pluggy 语义）/ `slots`（扩展位树）/
`bindings`（六层绑定解析）/ `manifest` / `loader` / `registries`（门面）/ `builtins`。
`__init__` 刻意不 re-export：import 成本与调用方实际用到的成正比，也避免 loader 与
registry 之间出现包级循环。
