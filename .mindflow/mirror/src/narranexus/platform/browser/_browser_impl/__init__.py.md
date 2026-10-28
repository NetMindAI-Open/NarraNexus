---
code_file: src/narranexus/platform/browser/_browser_impl/__init__.py
last_verified: 2026-09-22
stub: false
---

# _browser_impl/__init__.py — 私有实现包的边界

## 为什么存在

标记边界，不做导出。方案三的具体实现（运行时检测、后端驱动、帧分发）都在这个包里，
调用方一律走 `platform/browser/__init__.py` 的公开面。与 `_artifact_impl/` 同构。

保持空导出是刻意的：一旦这里开始 re-export，外部就会绕过公开面直接依赖内部文件结构，
后续换后端实现（本地 Chromium → 容器 / 远程 grid）就会被调用方钉死。
