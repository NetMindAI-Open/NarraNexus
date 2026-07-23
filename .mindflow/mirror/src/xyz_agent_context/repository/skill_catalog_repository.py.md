---
code_file: src/xyz_agent_context/repository/skill_catalog_repository.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — list_defaults()

is_default=1 且 published 的技能,每 skill 取语义化最新版,按 skill_id 排序。


# skill_catalog_repository.py

CRUD + search over `skill_catalog` — the cloud-authoritative marketplace
directory (one row per skill_id × version). Desktop deployments have the table
but never write it; they query the cloud API instead.

## Why it exists

The catalog is the ONLY directory source (the v1.0 design's S3
`registry-index.json` was dropped precisely so there is no second copy to
drift). Publish, search, detail, update-check and download counting all read
or write this table through this repository.

## Design decisions

- **Compact JSON serialization** (`separators=(",", ":")`): capability/tag
  filters use `LIKE '%"search:web"%'` substring matching on the serialized
  column. This only works because serialization is whitespace-free — do not
  "prettify" the JSON.
- **Search returns one card per skill** — latest published version wins.
  Version dedup, semver ordering, sort and pagination happen in Python, not
  SQL: the catalog is bounded (<100 skills for the foreseeable future) and
  dual-dialect SQL for group-wise-max is not worth the complexity.
- **`_semver_key`** parses numeric triples; pre-release/build suffixes are
  ignored for ordering. "1.10.0 > 1.9.0" is covered by tests.
- **`publish()` is an upsert** keyed on (skill_id, version) that never resets
  `downloads` on re-publish.
- `increment_downloads` uses raw `execute()` with `downloads = downloads + 1`
  so concurrent installs don't lose counts (read-modify-write would).

## Gotchas

- `search()` only matches `status='published'` rows; `get_version()` has no
  status filter (detail pages may show deprecated versions).
