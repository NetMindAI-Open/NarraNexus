---
code_file: src/xyz_agent_context/migration/scanner.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

Public orchestration of the Scanner: `detect()` (list frameworks in the standard
home locations) and `scan(path, framework)` (detect → extract → assemble a
`StandardizedAgentImport`). The one entry point consumers call
(`backend/routes/migrate.py`, the dev CLI, later the Import Button).

## Design decisions

- `scan(path=None)` auto-detects the highest-confidence framework across home
  locations and scans it; `path` scopes to one dir; `framework` forces the
  classification (still uses the detected path). Raises `FileNotFoundError` when
  nothing is detected and no path is given — the route maps that to 404.
- Extraction is delegated to `extractors.extract` which returns a 6-tuple incl.
  `sessions` (`List[MigrationSession]`); scanner just packs it into the schema.
  No writes.
