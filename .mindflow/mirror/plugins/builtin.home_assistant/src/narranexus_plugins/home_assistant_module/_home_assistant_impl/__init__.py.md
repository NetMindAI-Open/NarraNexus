---
code_file: plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/_home_assistant_impl/__init__.py
last_verified: 2026-09-07
stub: false
---

# plugins/builtin.home_assistant/src/narranexus_plugins/home_assistant_module/_home_assistant_impl/__init__.py — package marker

private implementation package of the Home Assistant module. No code belongs here: the host boots plugins from their manifests, and an import-time registration would break the lazy-contribution rule and the distribution excludes.
