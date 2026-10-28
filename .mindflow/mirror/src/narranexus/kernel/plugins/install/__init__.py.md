---
code_file: src/narranexus/kernel/plugins/install/__init__.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2c）— 安装子包入口

导出 `Installer` 与三种来源；所有入口（工场 API、CLI、Nexus_Plugins_Module）都走 `Installer.install` 这一条
流水线：fetch → validate → deps → place → register。
