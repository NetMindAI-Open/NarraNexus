---
code_file: packages/narranexus-contracts/src/narranexus/contracts/memory.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 记忆种类的结构化最小面

`MemoryKindSpec`（`memory/spec.py`）字段很多且 import `memory.record`，不能整体搬进叶子层；
契约只要求 `kind: str` 与 `passive: bool`——内核注册/列举 kind 只需要这两个。
`tests/nx_kernel/contracts/test_kind_contracts.py` 对六个内置 spec 逐个断言满足此 Protocol。
