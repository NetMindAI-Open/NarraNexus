---
code_file: src/narranexus/kernel/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 内核 = 没有它进程起不来的东西

留平台/进内核的判据在 spec §2.1/§3.1：只有「所有插件都需要且无法在插件里做」的进内核。
批 0 只放插件运行时原语（`plugins/`）、事件总线（`events/`）与部署模式解析
（`deployment.py`）。内核不认识任何具体插件（宪章 6），import-linter 禁止它 import
`xyz_agent_context` 与 `backend`。`Kernel` 类只是 `kernel` 根位的契约符号（内核本身永不被重绑），
让扩展位树里每个契约字符串都能 import 到。
