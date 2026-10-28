---
code_file: conftest.py
last_verified: 2026-09-07
stub: false
---

# conftest.py

## 2026-09-07 — repo-root pytest configuration

Loads the builtins into the process registries once at collection for the backend role (not frozen) — for tests/ and plugins/*/tests alike, since registration happens only at boot and some modules resolve a registry entry at import.

## 2026-09-07 — 已加载探针改用 paths()（B7）

turn.pipeline.act.framework 由 builtin.turn 声明，boot 前不存在，不能再用它探测；registries.paths() 为空即未加载。
