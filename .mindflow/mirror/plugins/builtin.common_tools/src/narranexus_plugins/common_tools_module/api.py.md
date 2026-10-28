---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/api.py
last_verified: 2026-09-04
stub: false
---

# builtin.common_tools — api.py facade

The only import surface another plugin or a distribution may use (plugin platform batch 6b): the plugin id, its package name and `module_class()`. Everything else in the package is private; the host reaches the plugin through its manifest contributions, never through imports.
