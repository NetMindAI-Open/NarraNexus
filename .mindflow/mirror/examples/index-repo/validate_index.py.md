---
code_file: examples/index-repo/validate_index.py
last_verified: 2026-09-04
stub: false
---

# examples/index-repo — validate_index.py

Metadata check of the official plugin index repo layout: `index.json` entries must be a subset of the `IndexEntry` fields with `<publisher>.<name>` ids (never `builtin.`), `owner/repo` repos, no duplicates, list-of-string tags/kinds; `blocked_versions.json` maps id → version → reason. Run by the index repo's workflow on every PR; inclusion is a metadata check, not a code review.
