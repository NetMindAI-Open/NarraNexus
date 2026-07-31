---
code_file: src/xyz_agent_context/migration/__init__.py
last_verified: 2026-07-30
stub: false
---

# migration/__init__.py — Agent Migration package facade

## Why it exists

The public entry to the Agent Migration package: re-exports `detect` / `scan`
from [[scanner]] so callers do `from xyz_agent_context.migration import scan`
without reaching into submodules. The package is the framework-agnostic
**read/convert/write** core (detect → extract → map → apply); the HTTP surface
lives in `backend/routes/migrate.py`.

Local/desktop only — the scanner reads the user's filesystem, so the whole
feature is disabled on cloud (routes 503). Detailed intent per file:
[[detector.py]] / [[extractors.py]] / [[scanner.py]] / [[mapper.py]] /
[[applier.py]], overview in [[_overview]].
