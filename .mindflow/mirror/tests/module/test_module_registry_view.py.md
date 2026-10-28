---
code_file: tests/module/test_module_registry_view.py
last_verified: 2026-09-24
stub: false
---

# 模块注册表的唯一来源

约束模块发现、MCP 模块集合和核心模块列表都来自可插拔注册表，避免某个
入口另写静态列表。BrowserModule 与其他内置模块一样必须有 manifest，
移除贡献者后对应视图立即消失，模块包不直接重导出具体模块类。
