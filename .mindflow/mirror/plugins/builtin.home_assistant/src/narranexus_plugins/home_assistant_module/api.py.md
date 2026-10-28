---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/api.py
last_verified: 2026-09-04
stub: false
---

# builtin.home_assistant — api.py facade

The only import surface another plugin or a distribution may use (plugin platform batch 6b): the plugin id, its package name and `module_class()`. Everything else in the package is private; the host reaches the plugin through its manifest contributions, never through imports.
