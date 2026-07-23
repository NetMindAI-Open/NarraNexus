---
code_file: src/xyz_agent_context/schema/skill_marketplace_schema.py
last_verified: 2026-07-20
stub: false
---

# skill_marketplace_schema.py

Pydantic models for the Skill Marketplace: `SkillCatalogEntry` (one published
version of a skill in the cloud catalog), `SkillInstallationRecord` (audit row
per workspace × skill), `SkillScanResult` (one security-scan run).

## Why it exists

The marketplace stores JSON-shaped payloads (capabilities, tags, dependencies,
config_schema) in TEXT columns; these models are the Python-native shape.
Serialization to/from the `*_json` columns is the repositories' job, so
service/route layers never touch raw JSON strings.

## Design decisions

- **Timestamps are `Optional[datetime]`** (matches `SkillArchive`): the SQLite
  backend returns datetime objects for DATETIME columns; pydantic coerces the
  string form transparently on the MySQL side.
- **`SkillInstallationRecord` is explicitly documented as a follower** of the
  filesystem truth — see spec §5.1 (disk wins).
- Enum-ish fields (`status`, `source_type`, `scan_status`) are plain `str`
  with inline comments, consistent with the rest of `schema/` (no Python Enum
  classes anywhere in the package).

Spec: `reference/self_notebook/specs/2026-07-20-skill-marketplace-tech-design-v1.1.md` §3.
