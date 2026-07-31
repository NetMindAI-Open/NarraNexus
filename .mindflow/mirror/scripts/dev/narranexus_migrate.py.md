---
code_file: scripts/dev/narranexus_migrate.py
stub: false
last_verified: 2026-07-21
---

## Why it exists

Thin dev CLI over the Agent Migration Scanner (`xyz_agent_context.migration`):
`detect` (list frameworks in the standard home locations) and `scan [--path]
[--framework]` (detect + extract → standardized JSON on stdout). For local
exploration/testing while the Import Button UI is not built yet; the primary
surface is the embedded route `/api/migrate/*`.

## Notes

- Read-only — never writes to NarraNexus, never prints non-MCP secret VALUES.
- Lives under `scripts/dev/` per the scripts layout convention (dev tooling, not
  a release/publish/migration script).
