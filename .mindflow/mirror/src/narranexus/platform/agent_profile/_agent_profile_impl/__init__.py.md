---
code_file: src/narranexus/platform/agent_profile/_agent_profile_impl/__init__.py
last_verified: 2026-08-18
stub: false
---

# _agent_profile_impl/__init__.py — 私有实现的包标记

`_` 前缀包，**永不外露**（仓库惯例，同 `_module_impl/` `_awareness_impl` 等）。
包外一律走 [[agent_profile_service]] 那个 seam，不 import 本包的叶子模块。

本身不导出任何东西：加符号到这里等于把私有实现变成公开契约。事务实现见
[[profile_write]]。
