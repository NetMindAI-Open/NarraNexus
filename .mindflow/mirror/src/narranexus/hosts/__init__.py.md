---
code_file: src/narranexus/hosts/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.5）— 宿主启动序列包

只有 `boot.py`。刻意不在 `__init__` 里 re-export `boot` 函数：那会把 `narranexus.hosts.boot` 这个**模块**名
遮成函数，`import narranexus.hosts.boot as boot_mod` 与 monkeypatch 都会踩空（测试实锤）。
