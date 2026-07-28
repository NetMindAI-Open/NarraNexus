---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/scanner/__init__.py
last_verified: 2026-07-20
stub: false
---

# scanner/__init__.py

Public surface of the skill security scanner: re-exports `scan_skill_dir`,
`ScanReport`, `ScanIssue` from `static.py`. This is the framework-agnostic
main defense line (spec §7): runs at publish time on cloud AND again before
installing URL/GitHub-sourced skills on either deployment mode — which is why
it must stay pure Python (AST + regex), no external services, desktop-runnable.
