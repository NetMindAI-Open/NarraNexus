---
code_file: src/narranexus/kernel/events/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 宿主事件总线的包

`hooks` kind（spec §7.4）的底座：平台在各观察点 `emit`，插件 `subscribe`。事件名与 payload
的契约在 `contracts/events.py`，总线实现在本包 `bus.py`。批 0 只建总线与测试，
观察点接线在批 2/3（D9）。
