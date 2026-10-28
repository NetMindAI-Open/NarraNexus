---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/__init__.py
last_verified: 2026-09-07
stub: false
---

# plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/__init__.py — package marker

private implementation package of the common-tools module (tools live in sibling modules; nothing public here). No code belongs here: the host boots plugins from their manifests, and an import-time registration would break the lazy-contribution rule and the distribution excludes.
