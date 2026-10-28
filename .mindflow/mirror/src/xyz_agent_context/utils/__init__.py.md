---
code_file: src/xyz_agent_context/utils/__init__.py
last_verified: 2026-09-04
stub: false
---

# src/xyz_agent_context/utils/__init__.py — alias package marker (batch 6a)

A real directory must exist on disk for the entrypoint shim next to it to be runnable by path; the alias finder in the package root resolves the import (`xyz_agent_context.utils` → `narranexus.platform.utils`) before the path finder ever sees this file.
