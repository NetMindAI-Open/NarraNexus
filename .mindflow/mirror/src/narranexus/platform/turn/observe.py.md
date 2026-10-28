---
code_file: src/narranexus/platform/turn/observe.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3a）— 边界视图

从 `RunContext` 派生七个 frozen 值对象（ids/计数/哈希，不传对象、实例或 DB 句柄）。Assemble 视图只有系统提示的
sha256 与长度——这是 approval 快照的切点，也是钩子能看到的全部。

## 2026-09-07 — commit_view comprehension simplified

The redundant 'getattr(...) and m.config.name' inside a comprehension already guarded by the same getattr read as a bug; removed.
